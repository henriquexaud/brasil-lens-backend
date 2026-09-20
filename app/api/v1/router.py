"""Agregação das rotas da v1."""

from fastapi import APIRouter

from app.api.v1 import contexts, health, hydrography, indicators, saved_views, territories, weather
from app.api.v1 import map as map_routes

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(contexts.router)
api_router.include_router(indicators.router)
api_router.include_router(territories.router)
api_router.include_router(map_routes.router)
api_router.include_router(saved_views.router)
api_router.include_router(weather.router)
api_router.include_router(hydrography.router)

