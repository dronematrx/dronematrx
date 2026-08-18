import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import api_router
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.telemetry.manager import TelemetryManager
from app.services.telemetry.simulator import SimulationTelemetryProvider
from app.websocket.connection_manager import connection_manager
from app.websocket.routes import router as websocket_router

logging.basicConfig(level=logging.INFO)
settings = get_settings()

telemetry_manager: TelemetryManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telemetry_manager
    if settings.TELEMETRY_SIMULATOR_ENABLED and settings.TELEMETRY_PROVIDER == "simulator":
        provider = SimulationTelemetryProvider(SessionLocal, tick_seconds=settings.TELEMETRY_TICK_SECONDS)
        telemetry_manager = TelemetryManager(provider, connection_manager, SessionLocal)
        await telemetry_manager.start()
    else:
        # Placeholder for MavlinkTelemetryProvider / NatsTelemetryProvider wiring
        # in a later sprint -- the rest of the app never needs to change.
        telemetry_manager = TelemetryManager(
            SimulationTelemetryProvider(SessionLocal, tick_seconds=settings.TELEMETRY_TICK_SECONDS),
            connection_manager,
            SessionLocal,
        )

    yield

    if telemetry_manager is not None:
        await telemetry_manager.stop()


app = FastAPI(
    title=settings.APP_NAME,
    description="Drone MatrX mission-control API -- fleet management, map/AOI, and real-time telemetry foundation.",
    version="0.2.0",
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
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Validation error", "errors": exc.errors()},
    )


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
    }


app.include_router(api_router)
app.include_router(websocket_router)
