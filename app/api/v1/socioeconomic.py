from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.api.http import etag_matches
from app.models import TerritoryLevel
from app.schemas.common import CamelModel, to_json
from app.schemas.socioeconomic import IndicatorListResponse, MapValuesResponse, TerritoryOverview
from app.services import socioeconomic as service

router = APIRouter(prefix="/socioeconomic", tags=["socioeconomic"])
Session = Annotated[AsyncSession, Depends(get_session)]


def conditional(request: Request, result: CamelModel) -> Response:
    body = to_json(result)
    etag = f'W/"socioeconomic:{sha256(body).hexdigest()}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=300"}
    return (
        Response(status_code=304, headers=headers)
        if etag_matches(request, etag)
        else Response(body, media_type="application/json", headers=headers)
    )


@router.get(
    "/indicators",
    response_model=IndicatorListResponse,
    summary="Indicadores socioeconômicos e anos disponíveis",
)
async def indicators(
    request: Request, session: Session, level: TerritoryLevel = TerritoryLevel.STATE
) -> Response:
    return conditional(request, await service.list_indicators(session, level))


@router.get(
    "/values",
    response_model=MapValuesResponse,
    summary="Valores e classificação, sem retransmitir a malha",
)
async def values(
    request: Request,
    session: Session,
    indicator: Annotated[str, Query(min_length=1, max_length=64)] = "population",
    level: TerritoryLevel = TerritoryLevel.STATE,
    parent: str | None = None,
    year: str = "latest",
) -> Response:
    return conditional(
        request,
        await service.get_values(
            session, level=level, parent=parent, indicator=indicator, year=year
        ),
    )


@router.get(
    "/territories/{code}",
    response_model=TerritoryOverview,
    summary="Indicadores e proveniência de um território",
)
async def territory(
    request: Request, session: Session, code: str, year: str = "latest"
) -> Response:
    return conditional(request, await service.get_overview(session, code, year))
