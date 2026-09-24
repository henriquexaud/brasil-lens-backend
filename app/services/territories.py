"""Casos de uso de território, incluindo a projeção de overview.

A política de ausência de dado vive aqui: o overview devolve **todos** os
indicadores do catálogo, com `value`/`year` nulos quando não há dado. Se o
serviço devolvesse apenas o que existe, a interface não teria como distinguir
"indicador inexistente" de "sem dado para este território".
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, TerritoryNotFoundError
from app.models import TerritoryLevel
from app.repositories import indicators as indicators_repo
from app.repositories import territories as territories_repo
from app.repositories.viewport import locate
from app.schemas.common import Pagination
from app.schemas.indicator import IndicatorSeries, SeriesPoint, TerritorySeriesResponse
from app.schemas.territory import (
    IndicatorValueOut,
    TerritoryDetail,
    TerritoryListResponse,
    TerritoryOverview,
    TerritoryRef,
    TerritorySummary,
)


def _summary(row: territories_repo.TerritoryRow) -> TerritorySummary:
    return TerritorySummary(
        ibge_code=row.ibge_code,
        name=row.name,
        level=row.level,
        abbreviation=row.abbreviation,
        parent=(
            TerritoryRef(
                ibge_code=row.parent_ibge_code,
                name=row.parent_name or "",
                level=row.parent_level,
            )
            if row.parent_ibge_code
            else None
        ),
    )


async def list_territories(
    session: AsyncSession,
    *,
    level: TerritoryLevel | None = None,
    parent_code: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> TerritoryListResponse:
    if parent_code is not None:
        parent_level = await territories_repo.get_level_by_code(session, parent_code)
        if parent_level is None:
            raise TerritoryNotFoundError(parent_code)

    rows = await territories_repo.list_territories(
        session,
        level=level,
        parent_ibge_code=parent_code,
        search=search,
        limit=limit,
        offset=offset,
    )
    total = await territories_repo.count_territories(
        session, level=level, parent_ibge_code=parent_code, search=search
    )
    return TerritoryListResponse(
        territories=[_summary(row) for row in rows],
        pagination=Pagination(total=total, limit=limit, offset=offset),
    )


async def _detail_fields(
    session: AsyncSession,
    row: territories_repo.TerritoryRow,
) -> dict[str, object]:
    """Campos comuns a `TerritoryDetail` e `TerritoryOverview`.

    Montados diretamente, sem `model_dump()` + `model_validate()`: a ida e
    volta revalidava tudo e achatava os submodelos em dicionários sem motivo.
    """
    children_count, children_level = await territories_repo.children_summary(session, row.ibge_code)
    summary = _summary(row)
    return {
        "ibge_code": summary.ibge_code,
        "name": summary.name,
        "level": summary.level,
        "abbreviation": summary.abbreviation,
        "parent": summary.parent,
        "capital": (
            TerritoryRef(
                ibge_code=row.capital_ibge_code,
                name=row.capital_name or "",
                level=TerritoryLevel.MUNICIPALITY,
            )
            if row.capital_ibge_code
            else None
        ),
        "children_count": children_count,
        "children_level": children_level,
        "bbox": row.bbox,
    }


async def get_detail(session: AsyncSession, ibge_code: str) -> TerritoryDetail:
    row = await territories_repo.get_by_code(session, ibge_code)
    if row is None:
        raise TerritoryNotFoundError(ibge_code)
    return TerritoryDetail(**await _detail_fields(session, row))


async def locate_territory(
    session: AsyncSession, latitude: float, longitude: float
) -> TerritoryDetail:
    code = await locate(session, latitude, longitude)
    if code is None:
        raise NotFoundError("Não encontramos um município brasileiro nessa localização.")
    return await get_detail(session, code)


async def get_overview(
    session: AsyncSession,
    ibge_code: str,
    *,
    year: int | None = None,
) -> TerritoryOverview:
    """Resposta pronta para a tela de detalhe: um request, nenhuma junção no cliente."""
    row = await territories_repo.get_by_code(session, ibge_code)
    if row is None:
        raise TerritoryNotFoundError(ibge_code)

    # `list_definitions` e não `list_catalog`: o overview não usa cobertura
    # temporal, e calculá-la custava ~200 ms para devolver ~1 KB.
    catalog = await indicators_repo.list_definitions(session)
    values = await indicators_repo.latest_values_for_territory(session, ibge_code, year=year)

    indicators = [
        IndicatorValueOut(
            key=entry.key,
            name=entry.name,
            unit=entry.unit,
            decimal_places=entry.decimal_places,
            origin=entry.origin,
            value=values[entry.key].value if entry.key in values else None,
            year=values[entry.key].reference_year if entry.key in values else None,
            source=values[entry.key].dataset_name if entry.key in values else None,
        )
        for entry in catalog
    ]

    return TerritoryOverview(**await _detail_fields(session, row), indicators=indicators)


async def get_series(
    session: AsyncSession,
    ibge_code: str,
    *,
    indicator_key: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
) -> TerritorySeriesResponse:
    row = await territories_repo.get_by_code(session, ibge_code)
    if row is None:
        raise TerritoryNotFoundError(ibge_code)

    points = await indicators_repo.series_for_territory(
        session,
        ibge_code,
        indicator_key=indicator_key,
        year_from=year_from,
        year_to=year_to,
    )

    grouped: dict[str, IndicatorSeries] = {}
    for point in points:
        series = grouped.get(point.indicator_key)
        if series is None:
            series = IndicatorSeries(
                key=point.indicator_key,
                name=point.indicator_name,
                unit=point.unit,
                decimal_places=point.decimal_places,
                origin=point.origin,
                source=point.dataset_name,
                points=[],
            )
            grouped[point.indicator_key] = series
        series.points.append(SeriesPoint(year=point.reference_year, value=point.value))

    return TerritorySeriesResponse(
        ibge_code=row.ibge_code,
        name=row.name,
        series=list(grouped.values()),
    )
