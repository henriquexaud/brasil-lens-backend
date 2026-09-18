"""Rota do mapa: a projeção de leitura do produto.

Um endpoint cobre as duas visões do MVP, porque são a mesma consulta com escopo
diferente:

    /map?level=state                       → as 27 UFs (visão inicial)
    /map?level=municipality&parent=35      → municípios de SP (drill-down)

Um endpoint significa um plano de execução para otimizar, uma chave de cache e
um contrato — em vez de dois caminhos que precisam ser mantidos em sincronia.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
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
    # Os dados só mudam durante a ingestão, então cache de cliente é seguro e é
    # o que elimina o custo de rede nas visitas seguintes.
    response.headers["Cache-Control"] = f"public, max-age={settings.http_cache_max_age}"
    return collection
