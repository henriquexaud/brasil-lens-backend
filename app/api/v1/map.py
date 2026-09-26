"""Rota do mapa: a projeção de leitura do produto.

Um endpoint serve a malha base em diferentes escopos geográficos:

    /map?level=state                       → as 27 UFs (visão inicial)
    /map?level=municipality&parent=35      → municípios de SP (drill-down)

Um endpoint significa um plano de execução para otimizar, uma chave de cache e
um contrato — em vez de dois caminhos que precisam ser mantidos em sincronia.

"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import settings
from app.models import TerritoryLevel
from app.schemas.map import MapFeatureCollection, MapLod
from app.services import map as map_service

router = APIRouter(prefix="/map", tags=["map"])

ParentQuery = Annotated[
    str | None,
    Query(
        description=(
            "Código IBGE do pai. Obrigatório para 'municipality' — sem ele a "
            "requisição transferiria a malha municipal do país inteiro."
        )
    ),
]


def _conditional(request: Request, etag: str) -> tuple[dict[str, str], bool]:
    """Cabeçalhos de cache e se o navegador já tem esta versão (304).

    O ETag nasce da identidade da projeção mais a versão da ingestão, então
    é decidido antes de montar a resposta: um 304 não toca o PostGIS.
    """
    headers = {
        "ETag": etag,
        "Cache-Control": (
            f"public, max-age={settings.map_http_cache_max_age}, "
            f"stale-while-revalidate={settings.map_http_stale_while_revalidate}"
        ),
    }
    sent = {tag.strip() for tag in request.headers.get("if-none-match", "").split(",")}
    return headers, etag in sent


@router.get(
    "",
    response_model=MapFeatureCollection,
    summary="GeoJSON da malha territorial",
    response_model_exclude_none=False,
)
async def get_map(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    level: Annotated[
        TerritoryLevel,
        Query(description="Nível territorial a desenhar."),
    ] = TerritoryLevel.STATE,
    parent: ParentQuery = None,
    lod: Annotated[
        MapLod | None,
        Query(
            description=(
                "Sobrepõe o nível de detalhe padrão do nível territorial. "
                "A geometria canônica não é servida: ver MapLod."
            )
        ),
    ] = None,
) -> MapFeatureCollection | Response:
    version = await map_service.data_version(session)
    geometry_lod = lod.to_geometry_lod() if lod else None
    key = map_service.projection_key(
        level=level,
        parent_code=parent,
        lod=geometry_lod,
        version=version,
    )
    headers, not_modified = _conditional(request, f'W/"map:{key}"')
    if not_modified:
        return Response(status_code=304, headers=headers)
    collection = await map_service.get_map(
        session,
        level=level,
        parent_code=parent,
        lod=geometry_lod,
        version=version,
    )
    response.headers.update(headers)
    return collection
