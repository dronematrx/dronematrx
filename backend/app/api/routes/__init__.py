from fastapi import APIRouter

from app.api.routes import auth, drones, geofences, missions

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(drones.router)
api_router.include_router(missions.router)
api_router.include_router(geofences.router)
