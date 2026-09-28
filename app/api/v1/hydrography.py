from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.core.config import settings
from app.schemas.hydrography import HydroFeatureCollection
from app.services import hydrography as hydrography_service
from app.services.viewport import parse_bbox

router = APIRouter(prefix="/hydrography", tags=["hydrography"])


@router.get(
    "",
    response_model=HydroFeatureCollection,
    summary="GeoJSON com cursos d'água e massas d'água oficiais da ANA/SNIRH",
    response_model_exclude_none=False,
)
async def get_hydrography(
    response: Response,
    zoom: Annotated[
        float,
        Query(ge=3, le=12, description="Zoom do mapa; define o detalhe dos rios e lagos."),
    ] = 4,
    bbox: Annotated[
        str | None,
        Query(description="Área 'oeste,sul,leste,norte'; ignorada abaixo do zoom 6."),
    ] = None,
) -> HydroFeatureCollection:
    collection = await hydrography_service.get_hydrography(
        zoom=zoom, bbox=parse_bbox(bbox) if bbox else None
    )
    max_age = (
        settings.map_http_cache_max_age
        if collection.metadata.status == "ok"
        else hydrography_service.PARTIAL_CACHE_SECONDS
    )
    response.headers["Cache-Control"] = f"public, max-age={max_age}"
    return collection
