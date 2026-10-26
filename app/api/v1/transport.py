from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.api.http import etag_matches
from app.schemas.common import to_json
from app.schemas.transport import (
    TransportCatalog,
    TransportIdentify,
    TransportLayer,
    TransportNetwork,
    TransportSummary,
)
from app.services import transport as service
from app.services.viewport import parse_bbox

router = APIRouter(prefix="/transport", tags=["transport"])
Session = Annotated[AsyncSession, Depends(get_session)]


def conditional(request: Request, body: bytes, max_age: int = 300) -> Response:
    etag = f'W/"transport:{sha256(body).hexdigest()}"'
    headers = {"ETag": etag, "Cache-Control": f"public, max-age={max_age}"}
    return (
        Response(status_code=304, headers=headers)
        if etag_matches(request, etag)
        else Response(body, media_type="application/json", headers=headers)
    )


@router.get("/catalog", response_model=TransportCatalog, summary="Camadas, fontes e WMS de ruas")
async def catalog(request: Request, session: Session) -> Response:
    return conditional(request, to_json(await service.get_catalog(session)))


@router.get(
    "/network",
    response_model=TransportNetwork,
    summary="Traçado de uma camada de transporte, fundido por área",
)
async def network(
    request: Request,
    session: Session,
    layer: TransportLayer,
    zoom: Annotated[float, Query(ge=3, le=14)] = 4,
    bbox: Annotated[
        str | None, Query(description="Área 'oeste,sul,leste,norte'; ignorada abaixo do zoom 6.")
    ] = None,
) -> Response:
    body = await service.get_network(
        session, layer=layer, zoom=zoom, bbox=parse_bbox(bbox) if bbox else None
    )
    return conditional(request, body, max_age=3600)


@router.get(
    "/identify", response_model=TransportIdentify, summary="Trecho ou porto mais próximo do clique"
)
async def identify(
    session: Session,
    layer: TransportLayer,
    latitude: Annotated[float, Query(ge=-90, le=90)],
    longitude: Annotated[float, Query(ge=-180, le=180)],
    tolerance: Annotated[float, Query(gt=0, le=1)] = 0.01,
) -> TransportIdentify:
    return await service.identify(
        session, layer=layer, longitude=longitude, latitude=latitude, tolerance=tolerance
    )


@router.get(
    "/territories/{code}",
    response_model=TransportSummary,
    summary="Extensão da malha e portos de um território",
)
async def summary(request: Request, session: Session, code: str) -> Response:
    return conditional(request, to_json(await service.get_summary(session, code)))
