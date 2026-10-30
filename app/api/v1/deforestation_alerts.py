from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.models import TerritoryLevel
from app.schemas.deforestation_alerts import DeforestationAlerts
from app.schemas.socioeconomic import MapValuesResponse
from app.services import deforestation_alerts as service

router = APIRouter(prefix="/deforestation-alerts", tags=["deforestation-alerts"])


@router.get("", response_model=DeforestationAlerts)
async def get_deforestation_alerts(
    response: Response, session: Annotated[AsyncSession, Depends(get_session)]
) -> DeforestationAlerts:
    result = await service.get_alerts(session)
    response.headers["Cache-Control"] = "public, max-age=600"
    return result


@router.get("/values", response_model=MapValuesResponse)
async def get_deforestation_alert_values(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    level: TerritoryLevel = TerritoryLevel.STATE,
    parent: Annotated[str | None, Query(pattern=r"^\d{2}$")] = None,
) -> MapValuesResponse:
    result = await service.get_alerts(session)
    response.headers["Cache-Control"] = "public, max-age=600"
    return service.map_values(result, level, parent)
