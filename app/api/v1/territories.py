"""Rotas de território.

Uma observação sobre o desenho: a lista original do projeto previa `/regions`,
`/states`, `/states/{c}`, `/states/{c}/municipalities` e `/municipalities/{c}`.
Como o modelo é uma entidade territorial única, essas cinco rotas são a mesma
query com filtros diferentes. Duas rotas cobrem todas elas sem perda:

    /territories?level=region                 → regiões
    /territories?level=state                  → UFs
    /territories?level=municipality&parent=35 → municípios de SP
    /territories/35                           → uma UF
    /territories/3550308                      → um município

O código IBGE é globalmente único, então a rota de detalhe não precisa do nível.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import settings
from app.models import TerritoryLevel
from app.schemas.indicator import TerritorySeriesResponse
from app.schemas.territory import (
    LocationInput,
    TerritoryDetail,
    TerritoryListResponse,
    TerritoryOverview,
)
from app.services import territories as territories_service

router = APIRouter(prefix="/territories", tags=["territories"])

IbgeCode = Annotated[
    str,
    Path(
        min_length=1,
        max_length=9,
        description="Código IBGE do território ('BR', '3', '35', '3550308').",
    ),
]


@router.get("", response_model=TerritoryListResponse, summary="Lista territórios")
async def list_territories(
    session: Annotated[AsyncSession, Depends(get_session)],
    level: Annotated[TerritoryLevel | None, Query(description="Nível territorial.")] = None,
    parent: Annotated[
        str | None, Query(description="Código IBGE do território pai (ex.: 35).")
    ] = None,
    search: Annotated[str | None, Query(min_length=2, description="Busca por nome.")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TerritoryListResponse:
    return await territories_service.list_territories(
        session, level=level, parent_code=parent, search=search, limit=limit, offset=offset
    )


@router.post("/locate", response_model=TerritoryDetail)
async def locate_territory(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    location: LocationInput,
) -> TerritoryDetail:
    # Coordenadas precisas não são guardadas nem enviadas a um geocoder externo.
    response.headers["Cache-Control"] = "no-store"
    return await territories_service.locate_territory(
        session, location.latitude, location.longitude
    )


@router.get("/{ibge_code}", response_model=TerritoryDetail, summary="Detalha um território")
async def get_territory(
    ibge_code: IbgeCode,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TerritoryDetail:
    return await territories_service.get_detail(session, ibge_code)


@router.get(
    "/{ibge_code}/overview",
    response_model=TerritoryOverview,
    summary="Projeção pronta para a tela de detalhe",
)
async def get_overview(
    ibge_code: IbgeCode,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    year: Annotated[
        int | None,
        Query(
            ge=1900,
            le=2100,
            description=(
                "Ano de referência. Omitido, cada indicador devolve seu último "
                "ano disponível — que pode diferir entre indicadores."
            ),
        ),
    ] = None,
) -> TerritoryOverview:
    # Mesma política do mapa e do catálogo: os dados mudam apenas na ingestão,
    # então o cache do cliente é seguro e é o que elimina a ida à rede quando o
    # usuário revisita um território.
    response.headers["Cache-Control"] = f"public, max-age={settings.http_cache_max_age}"
    return await territories_service.get_overview(session, ibge_code, year=year)


@router.get(
    "/{ibge_code}/indicators",
    response_model=TerritorySeriesResponse,
    summary="Séries históricas do território",
)
async def get_territory_indicators(
    ibge_code: IbgeCode,
    session: Annotated[AsyncSession, Depends(get_session)],
    indicator: Annotated[str | None, Query(description="Filtra por chave de indicador.")] = None,
    year_from: Annotated[int | None, Query(alias="yearFrom", ge=1900, le=2100)] = None,
    year_to: Annotated[int | None, Query(alias="yearTo", ge=1900, le=2100)] = None,
) -> TerritorySeriesResponse:
    return await territories_service.get_series(
        session, ibge_code, indicator_key=indicator, year_from=year_from, year_to=year_to
    )
