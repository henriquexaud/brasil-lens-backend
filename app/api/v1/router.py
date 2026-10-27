from fastapi import APIRouter

from app.api.v1 import (
    auth,
    education,
    fire_hotspots,
    followed_municipalities,
    health,
    hydrography,
    notifications,
    political,
    socioeconomic,
    territories,
    transport,
    weather,
)
from app.api.v1 import map as map_routes

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(health.router)
api_router.include_router(territories.router)
api_router.include_router(map_routes.router)
api_router.include_router(followed_municipalities.router)
api_router.include_router(notifications.router)
api_router.include_router(socioeconomic.router)
api_router.include_router(political.router)
api_router.include_router(weather.router)
api_router.include_router(hydrography.router)
api_router.include_router(fire_hotspots.router)
api_router.include_router(transport.router)
api_router.include_router(education.router)
