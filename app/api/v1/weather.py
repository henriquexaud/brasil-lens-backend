"""Rotas do painel de clima — sem `level`/`parent`/`indicator`/`year`.

Diferente de `/map`, este contexto não é uma coropleta territorial: são
camadas independentes (estações, alertas), cada uma sua própria requisição —
mesmo desenho que `docs/ARCHITECTURE.md` §8.3 já previa para "camadas
temáticas que façam sentido sobrepor".
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.schemas.weather import (
    WeatherAlertCollection,
    WeatherSourcesResponse,
    WeatherStationCollection,
)
from app.services import weather as weather_service

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get(
    "/stations",
    response_model=WeatherStationCollection,
    summary="Última leitura de cada estação meteorológica",
)
async def get_stations(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WeatherStationCollection:
    return await weather_service.get_stations(session)


@router.get(
    "/alerts",
    response_model=WeatherAlertCollection,
    summary="Alertas meteorológicos oficiais ainda ativos",
)
async def get_alerts(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WeatherAlertCollection:
    return await weather_service.get_alerts(session)


@router.get(
    "/sources",
    response_model=WeatherSourcesResponse,
    summary="Frescor e disponibilidade de cada fonte de clima",
)
async def get_sources(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WeatherSourcesResponse:
    return await weather_service.get_sources(session)
