"""Rotas do painel de clima — sem `level`/`parent`/`indicator`/`year`.

Diferente de `/map`, este contexto não é uma coropleta territorial: são
camadas independentes (estações, alertas), cada uma sua própria requisição —
mesmo desenho que `docs/ARCHITECTURE.md` §8.3 já previa para "camadas
temáticas que façam sentido sobrepor".
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.schemas.weather import (
    WeatherAlertCollection,
    WeatherCurrentResponse,
    WeatherSourcesResponse,
    WeatherStationCollection,
)
from app.services import weather as weather_service
from app.services.weather_forecast import (
    get_current,
    get_municipalities_current,
    get_territory_current,
)

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get(
    "/current",
    response_model=WeatherCurrentResponse,
    summary="Condições atuais e previsão nas capitais ou no território selecionado",
)
async def get_current_weather(
    session: Annotated[AsyncSession, Depends(get_session)],
    territory: Annotated[str | None, Query(pattern=r"^\d{2,7}$")] = None,
    forecast: bool = True,
) -> WeatherCurrentResponse:
    return (
        await get_territory_current(session, territory, include_forecast=forecast)
        if territory
        else await get_current(include_forecast=forecast)
    )


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


@router.get(
    "/municipalities",
    response_model=WeatherCurrentResponse,
    summary="Condições atuais dos municípios em lotes, sem previsão diária",
)
async def get_municipalities_weather(
    session: Annotated[AsyncSession, Depends(get_session)],
    parent: Annotated[str, Query(pattern=r"^\d{2}$")],
    offset: Annotated[int, Query(ge=0, le=6000)] = 0,
    limit: Annotated[int, Query(ge=1, le=40)] = 40,
) -> WeatherCurrentResponse:
    return await get_municipalities_current(session, parent, offset, limit)
