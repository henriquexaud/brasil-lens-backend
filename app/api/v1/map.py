"""Rota do mapa: a projeção de leitura do produto.

Um endpoint cobre as duas visões do MVP, porque são a mesma consulta com escopo
diferente:

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
from app.services.classification import DEFAULT_CLASS_COUNT

router = APIRouter(prefix="/map", tags=["map"])


@router.get(
    "",
    response_model=MapFeatureCollection,
    summary="GeoJSON com geometria, valores e metadados de coropleta",
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
    parent: Annotated[
        str | None,
        Query(
            description=(
                "Código IBGE do pai. Obrigatório para 'municipality' — sem ele a "
                "requisição transferiria a malha municipal do país inteiro."
            )
        ),
    ] = None,
    indicator: Annotated[
        str | None,
        Query(description="Chave do indicador da coropleta. Omitido, devolve só geometria."),
    ] = None,
    year: Annotated[
        str,
        Query(description="Ano de referência ou 'latest' (último ano disponível no escopo)."),
    ] = "latest",
    lod: Annotated[
        MapLod | None,
        Query(
            description=(
                "Sobrepõe o nível de detalhe padrão do nível territorial. "
                "A geometria canônica não é servida: ver MapLod."
            )
        ),
    ] = None,
    classes: Annotated[int, Query(ge=2, le=9, description="Número de classes.")] = (
        DEFAULT_CLASS_COUNT
    ),
) -> MapFeatureCollection:
    collection = await map_service.get_map(
        session,
        level=level,
        parent_code=parent,
        indicator_key=indicator,
        year=year,
        lod=lod.to_geometry_lod() if lod else None,
        classes=classes,
    )
    # ETag condicional: evita retransmitir megabytes de GeoJSON se o navegador já possui a malha
    etag = (
        f'W/"{collection.scope.level.value}-{collection.scope.parent or "all"}-'
        f'{collection.indicator.key if collection.indicator else "none"}-'
        f'{collection.indicator.year if collection.indicator else "none"}-'
        f'{collection.scope.lod.value}-{collection.scope.count}"'
    )
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip() == etag:
        return Response(
            status_code=304,
            headers={
                "ETag": etag,
                "Cache-Control": f"public, max-age={settings.http_cache_max_age}",
            },
        )

    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = f"public, max-age={settings.http_cache_max_age}"
    return collection
