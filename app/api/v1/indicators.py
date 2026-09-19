"""Catálogo de indicadores."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import settings
from app.models import DataContext, TerritoryLevel
from app.schemas.indicator import IndicatorListResponse, IndicatorOut
from app.services import indicators as indicators_service

router = APIRouter(prefix="/indicators", tags=["indicators"])

_LEVEL_DESCRIPTION = (
    "Restringe a cobertura temporal ao nível territorial informado. "
    "A cobertura pode diferir entre UF e município, e é a do nível exibido "
    "que deve popular o seletor de ano."
)
_CONTEXT_DESCRIPTION = (
    "Restringe ao agrupamento temático informado (ver GET /contexts). "
    "Omitido, devolve o catálogo inteiro — o comportamento de hoje."
)


@router.get("", response_model=IndicatorListResponse, summary="Lista indicadores")
async def list_indicators(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    level: Annotated[TerritoryLevel | None, Query(description=_LEVEL_DESCRIPTION)] = None,
    context: Annotated[DataContext | None, Query(description=_CONTEXT_DESCRIPTION)] = None,
) -> IndicatorListResponse:
    response.headers["Cache-Control"] = f"public, max-age={settings.http_cache_max_age}"
    return await indicators_service.list_indicators(session, level=level, context=context)


@router.get("/{indicator_key}", response_model=IndicatorOut, summary="Detalha um indicador")
async def get_indicator(
    indicator_key: str,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    level: Annotated[TerritoryLevel | None, Query(description=_LEVEL_DESCRIPTION)] = None,
    context: Annotated[DataContext | None, Query(description=_CONTEXT_DESCRIPTION)] = None,
) -> IndicatorOut:
    response.headers["Cache-Control"] = f"public, max-age={settings.http_cache_max_age}"
    return await indicators_service.get_indicator(
        session, indicator_key, level=level, context=context
    )
