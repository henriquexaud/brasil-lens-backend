"""Rota do mapa: a projeção de leitura do produto.

Um endpoint cobre as duas visões do MVP, porque são a mesma consulta com escopo
diferente:

    /map?level=state                       → as 27 UFs (visão inicial)
    /map?level=municipality&parent=35      → municípios de SP (drill-down)

Um endpoint significa um plano de execução para otimizar, uma chave de cache e
um contrato — em vez de dois caminhos que precisam ser mantidos em sincronia.

`/map/values` é a mesma projeção sem geometria: com a malha já no navegador,
trocar indicador ou ano custa alguns KB em vez de centenas.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import settings
from app.models import TerritoryLevel
from app.schemas.map import MapFeatureCollection, MapLod, MapValuesResponse
from app.services import map as map_service
from app.services.classification import DEFAULT_CLASS_COUNT

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
YearQuery = Annotated[
    str,
    Query(description="Ano de referência ou 'latest' (último ano disponível no escopo)."),
]
ClassesQuery = Annotated[int, Query(ge=2, le=9, description="Número de classes.")]


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
    parent: ParentQuery = None,
    indicator: Annotated[
        str | None,
        Query(description="Chave do indicador da coropleta. Omitido, devolve só geometria."),
    ] = None,
    year: YearQuery = "latest",
    lod: Annotated[
        MapLod | None,
        Query(
            description=(
                "Sobrepõe o nível de detalhe padrão do nível territorial. "
                "A geometria canônica não é servida: ver MapLod."
            )
        ),
    ] = None,
    classes: ClassesQuery = DEFAULT_CLASS_COUNT,
) -> MapFeatureCollection | Response:
    version = await map_service.data_version(session)
    geometry_lod = lod.to_geometry_lod() if lod else None
    key = map_service.projection_key(
        level=level,
        parent_code=parent,
        indicator_key=indicator,
        year=year,
        lod=geometry_lod,
        classes=classes,
        version=version,
    )
    headers, not_modified = _conditional(request, f'W/"map:{key}"')
    if not_modified:
        return Response(status_code=304, headers=headers)
    collection = await map_service.get_map(
        session,
        level=level,
        parent_code=parent,
        indicator_key=indicator,
        year=year,
        lod=geometry_lod,
        classes=classes,
        version=version,
    )
    response.headers.update(headers)
    return collection


@router.get(
    "/values",
    response_model=MapValuesResponse,
    summary="Só os valores e a classificação da coropleta, sem geometria",
)
async def get_map_values(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    indicator: Annotated[str, Query(description="Chave do indicador da coropleta.")],
    level: Annotated[
        TerritoryLevel,
        Query(description="Nível territorial dos valores."),
    ] = TerritoryLevel.STATE,
    parent: ParentQuery = None,
    year: YearQuery = "latest",
    classes: ClassesQuery = DEFAULT_CLASS_COUNT,
) -> MapValuesResponse | Response:
    version = await map_service.data_version(session)
    key = map_service.projection_key(
        level=level,
        parent_code=parent,
        indicator_key=indicator,
        year=year,
        lod=None,
        classes=classes,
        version=version,
    )
    headers, not_modified = _conditional(request, f'W/"values:{key}"')
    if not_modified:
        return Response(status_code=304, headers=headers)
    values = await map_service.get_map_values(
        session,
        level=level,
        parent_code=parent,
        indicator_key=indicator,
        year=year,
        classes=classes,
        version=version,
    )
    response.headers.update(headers)
    return values
