import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import api_router
from app.core.config import get_settings
from app.core.logging import install_redaction
from app.db.session import SessionLocal
from app.services.mission_planning_service import MissionLockedError, PlanningError
from app.services.mission_state_service import InvalidTransitionError, TransitionBlockedError
from app.services.runtime import FleetRuntime
from app.services.simulation.orchestrator import SimulationError, SimulationOrchestrator
from app.services.mission_control import MissionControl
from app.services.mission_control.commands import CommandError
from app.services.mission_control.detections import DetectionError
from app.services.mission_control.fc import FcError
from app.services.mission_control.permissions import FlightPermissionError
from app.services.mission_control.video import VideoError
from app.services.telemetry.manager import TelemetryManager
from app.services.telemetry.simulator import SimulationTelemetryProvider
from app.websocket.connection_manager import connection_manager
from app.websocket.routes import router as websocket_router

logging.basicConfig(level=logging.INFO)
install_redaction()
settings = get_settings()

telemetry_manager: TelemetryManager | None = None
fleet_runtime: FleetRuntime | None = None
sim_orchestrator: SimulationOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telemetry_manager, fleet_runtime, sim_orchestrator
    if settings.TELEMETRY_SIMULATOR_ENABLED and settings.TELEMETRY_PROVIDER == "simulator":
        # Sprint 4: simulator -> ORCA (10 Hz) -> comms link -> NATS JetStream ->
        # consumers (WebSocket/Drone table, TimescaleDB, orchestrator).
        fleet_runtime = FleetRuntime(settings, SessionLocal, connection_manager)
        telemetry_manager = fleet_runtime.telemetry_manager
        await fleet_runtime.start()
    else:
        # Runtime disabled (tests, or a future MAVLink/NATS provider): keep the
        # TelemetryManager so REST/WS contracts stay identical.
        telemetry_manager = TelemetryManager(
            SimulationTelemetryProvider(SessionLocal, tick_seconds=settings.TELEMETRY_TICK_SECONDS),
            connection_manager,
            SessionLocal,
        )
    app.state.runtime = fleet_runtime
    # Sprint 5: Gazebo/PX4 SITL simulation sessions (telemetry via the runtime's NATS `ui` stream).
    sim_orchestrator = SimulationOrchestrator(SessionLocal, connection_manager, fleet_runtime)
    await sim_orchestrator.start()
    app.state.sim = sim_orchestrator
    # Sprint 6: mission control (commands, bindings, detections over the same bus).
    mission_control = MissionControl(SessionLocal, connection_manager, fleet_runtime)
    await mission_control.start()
    app.state.mc = mission_control

    yield

    await mission_control.stop()
    await sim_orchestrator.stop()
    if fleet_runtime is not None:
        await fleet_runtime.stop()


app = FastAPI(
    title=settings.APP_NAME,
    description="Drone MatrX mission-control API -- fleet management, map/AOI, and real-time telemetry foundation.",
    version="0.4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Model-level validators put the raw exception object in `ctx`; stringify
    # it so the response is always serializable.
    errors = [
        {**err, "ctx": {k: str(v) for k, v in err["ctx"].items()}} if "ctx" in err else err
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Validation error", "errors": jsonable_encoder(errors)},
    )


@app.exception_handler(PlanningError)
async def planning_error_handler(request: Request, exc: PlanningError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": exc.message, "code": exc.code})


@app.exception_handler(MissionLockedError)
async def mission_locked_handler(request: Request, exc: MissionLockedError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc), "code": "MISSION_LOCKED"})


@app.exception_handler(InvalidTransitionError)
async def invalid_transition_handler(request: Request, exc: InvalidTransitionError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc), "code": "INVALID_TRANSITION", "current": exc.current, "target": exc.target},
    )


@app.exception_handler(TransitionBlockedError)
async def transition_blocked_handler(request: Request, exc: TransitionBlockedError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": str(exc), "code": "TRANSITION_BLOCKED", "checks": exc.checks, "report": exc.report},
    )


@app.exception_handler(SimulationError)
async def simulation_error_handler(request: Request, exc: SimulationError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": exc.message, "code": exc.code})


@app.exception_handler(CommandError)
@app.exception_handler(FcError)
@app.exception_handler(VideoError)
@app.exception_handler(DetectionError)
@app.exception_handler(FlightPermissionError)
async def mission_control_error_handler(request: Request, exc: Exception) -> JSONResponse:
    content = {"detail": exc.message, "code": exc.code}
    content.update(jsonable_encoder(getattr(exc, "extra", {}) or {}))
    return JSONResponse(status_code=exc.status, content=content)


@app.exception_handler(SQLAlchemyError)
async def db_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logging.getLogger("dronematrx.db").exception("Database error handling %s %s", request.method, request.url)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Unable to connect to the local mission database"},
    )


@app.get("/health", tags=["system"])
def health() -> dict:
    return {
        "status": "ok",
        "environment": settings.ENVIRONMENT,
        "telemetry_provider": settings.TELEMETRY_PROVIDER,
        "telemetry_simulator_enabled": settings.TELEMETRY_SIMULATOR_ENABLED,
        "websocket_clients": connection_manager.active_count,
        "nats_connected": bool(fleet_runtime and fleet_runtime.bus.connected),
        "orca_enabled": settings.ORCA_ENABLED,
    }


app.include_router(api_router)
app.include_router(websocket_router)
