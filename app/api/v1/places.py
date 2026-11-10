from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.api.http import etag_matches
from app.schemas.places import (
    PlaceAreas,
    PlaceCatalog,
    PlaceContext,
    PlaceDetail,
    PlaceLayer,
    PlaceLevel,
    PlacePoints,
    PlaceSummary,
    PlaceValues,
    SpeciesGroup,
    SpeciesSummary,
)
from app.services import places as service
from app.services import species
from app.services.viewport import parse_bbox

router = APIRouter(prefix="/places", tags=["places"])
Session = Annotated[AsyncSession, Depends(get_session)]
Category = Annotated[str | None, Query(max_length=32)]


def conditional(request: Request, body: bytes, max_age: int = 300) -> Response:
    etag = f'W/"places:{sha256(body).hexdigest()}"'
    headers = {"ETag": etag, "Cache-Control": f"public, max-age={max_age}"}
    return (
        Response(status_code=304, headers=headers)
        if etag_matches(request, etag)
        else Response(body, media_type="application/json", headers=headers)
    )


@router.get("/catalog", response_model=PlaceCatalog, summary="Camadas, categorias e fontes")
async def catalog(request: Request, session: Session, context: PlaceContext) -> Response:
    return conditional(request, await service.get_catalog(session, context))


@router.get("/values", response_model=PlaceValues, summary="Contagem de lugares por território")
async def values(
    request: Request,
    session: Session,
    layer: PlaceLayer,
    level: PlaceLevel,
    category: Category = None,
    parent: Annotated[str | None, Query(max_length=2)] = None,
) -> Response:
    body = await service.get_values(
        session, layer=layer, category=category, level=level, parent=parent
    )
    return conditional(request, body)


@router.get("/points", response_model=PlacePoints, summary="Pontos de uma camada na área")
async def points(
    request: Request,
    session: Session,
    layer: PlaceLayer,
    bbox: Annotated[str, Query(description="Área 'oeste,sul,leste,norte'.")],
    category: Category = None,
) -> Response:
    body = await service.get_points(session, layer=layer, category=category, bbox=parse_bbox(bbox))
    return conditional(request, body, max_age=3600)


@router.get("/areas", response_model=PlaceAreas, summary="Contornos de uma camada de áreas")
async def areas(
    request: Request,
    session: Session,
    layer: PlaceLayer,
    zoom: Annotated[float, Query(ge=3, le=16)] = 4,
    bbox: Annotated[
        str | None, Query(description="Área 'oeste,sul,leste,norte'; ignorada abaixo do zoom 6.")
    ] = None,
    category: Category = None,
) -> Response:
    body = await service.get_areas(
        session, layer=layer, category=category, zoom=zoom, bbox=parse_bbox(bbox) if bbox else None
    )
    return conditional(request, body, max_age=3600)


@router.get("/items/{place_id}", response_model=PlaceDetail, summary="Detalhe de um lugar")
async def item(session: Session, place_id: int) -> PlaceDetail:
    return await service.get_place(session, place_id)


@router.get(
    "/territories/{code}",
    response_model=PlaceSummary,
    summary="Lugares de uma camada num território",
)
async def summary(
    request: Request, session: Session, code: str, layer: PlaceLayer, category: Category = None
) -> Response:
    body = await service.get_summary(session, code=code, layer=layer, category=category)
    return conditional(request, body)


@router.get(
    "/species/{code}",
    response_model=SpeciesSummary,
    summary="Espécies com mais registros no território (GBIF)",
)
async def territory_species(
    response: Response, session: Session, code: str, group: SpeciesGroup = "birds"
) -> SpeciesSummary:
    response.headers["Cache-Control"] = "public, max-age=3600"
    return await species.get_species(session, code, group)
