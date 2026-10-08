from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.api.http import etag_matches
from app.models import TerritoryLevel
from app.schemas.common import CamelModel, to_json
from app.schemas.political import Metric, Office, PoliticalCatalog, PoliticalDetail, PoliticalValues
from app.services import political as service

router = APIRouter(prefix="/political", tags=["political"])
Session = Annotated[AsyncSession, Depends(get_session)]
Round = Annotated[int, Query(alias="round", ge=0, le=2)]


def conditional(request: Request, result: CamelModel) -> Response:
    body = to_json(result)
    etag = f'W/"political:{sha256(body).hexdigest()}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=300"}
    return (
        Response(status_code=304, headers=headers)
        if etag_matches(request, etag)
        else Response(body, media_type="application/json", headers=headers)
    )


@router.get("/catalog", response_model=PoliticalCatalog)
async def catalog(request: Request, session: Session) -> Response:
    return conditional(request, await service.catalog(session))


@router.get("/values", response_model=PoliticalValues)
async def values(
    request: Request,
    session: Session,
    year: int = 2022,
    office: Office = "president",
    election_round: Round = 2,
    metric: Metric = "leading_candidate",
    level: TerritoryLevel = TerritoryLevel.STATE,
    parent: str | None = None,
) -> Response:
    return conditional(
        request,
        await service.values(
            session,
            year=year,
            office=office,
            election_round=election_round,
            metric=metric,
            level=level,
            parent=parent,
        ),
    )


@router.get("/territories/{code}", response_model=PoliticalDetail)
async def detail(
    request: Request,
    session: Session,
    code: str,
    year: int = 2022,
    office: Office = "president",
    election_round: Round = 2,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    limit: Annotated[int, Query(ge=1, le=25)] = 25,
) -> Response:
    return conditional(
        request,
        await service.detail(
            session,
            code=code,
            year=year,
            office=office,
            election_round=election_round,
            offset=offset,
            limit=limit,
        ),
    )
