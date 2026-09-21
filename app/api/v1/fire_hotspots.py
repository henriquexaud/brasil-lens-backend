"""Camada pública de focos INPE e detalhes consultados ao selecionar o mapa."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.schemas.fire_hotspots import (
    FireHotspotCollection,
    FireHotspotDetails,
    FireScope,
    FireSummary,
)
from app.services import fire_hotspots as service
from app.services import fire_summary

router = APIRouter(prefix="/fire-hotspots", tags=["fire-hotspots"])
Parent = Annotated[str | None, Query(pattern=r"^(?:\d{2}|\d{7})$")]
Hours = Annotated[int, Query(ge=1, le=168)]


@router.get("/summary", response_model=FireSummary)
async def summary(
    session: Annotated[AsyncSession, Depends(get_session)],
    at: datetime,
    level: FireScope = "country",
    parent: Parent = None,
    hours: Hours = service.DEFAULT_FIRE_HOURS,
) -> FireSummary:
    return await fire_summary.get_summary(session, level=level, parent=parent, hours=hours, at=at)


@router.get("", response_model=FireHotspotCollection)
async def get_fire_hotspots(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    level: FireScope = "country",
    parent: Parent = None,
    hours: Hours = service.DEFAULT_FIRE_HOURS,
) -> FireHotspotCollection:
    """Contagem, atualização, prévia recente e configuração WMS para todos os focos."""
    result = await service.get_fire_hotspots(session, level=level, parent_code=parent, hours=hours)
    response.headers["Cache-Control"] = "no-cache"
    return result


@router.get("/identify", response_model=FireHotspotDetails)
async def identify_fire_hotspots(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    latitude: Annotated[float, Query(ge=-90, le=90)],
    longitude: Annotated[float, Query(ge=-180, le=180)],
    tolerance: Annotated[float, Query(gt=0, le=0.5)],
    at: datetime,
    level: FireScope = "country",
    parent: Parent = None,
    hours: Hours = service.DEFAULT_FIRE_HOURS,
) -> FireHotspotDetails:
    """Até 20 detecções próximas ao ponto, ordenadas por proximidade, no mesmo período do mapa."""
    result = await service.identify_fire_hotspots(
        session,
        level=level,
        parent_code=parent,
        hours=hours,
        latitude=latitude,
        longitude=longitude,
        tolerance=tolerance,
        at=at,
    )
    response.headers["Cache-Control"] = "public, max-age=120"
    return result
