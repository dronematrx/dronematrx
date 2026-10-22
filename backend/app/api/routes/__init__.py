from fastapi import APIRouter

from app.api.routes import auth, drones, geofences, mission_planning, missions, ops, orca, simulations

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(drones.router)
# mission_planning first: its static paths (/missions/templates) must win over /missions/{mission_id}
api_router.include_router(mission_planning.router)
api_router.include_router(missions.router)
api_router.include_router(geofences.router)
api_router.include_router(orca.router)
api_router.include_router(ops.comms_router)
api_router.include_router(ops.telemetry_router)
api_router.include_router(ops.messaging_router)
api_router.include_router(simulations.router)
