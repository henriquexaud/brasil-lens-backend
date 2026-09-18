"""Caso de uso do catálogo de indicadores."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.core.config import settings
from app.core.errors import IndicatorNotFoundError
from app.models import TerritoryLevel
from app.repositories import indicators as indicators_repo
from app.schemas.indicator import IndicatorListResponse, IndicatorOut

# A cobertura temporal exige agregar meio milhão de valores (~56 ms). O
# resultado muda apenas quando a ingestão roda, e todo cliente pede exatamente
# a mesma coisa ao abrir o mapa — é o caso de uso canônico de um cache TTL em
# processo, sem infraestrutura nova.
_catalog_cache: TTLCache[list[IndicatorOut]] = TTLCache(
    ttl_seconds=settings.read_cache_ttl_seconds,
    max_entries=settings.read_cache_max_entries,
)


def clear_cache() -> None:
    _catalog_cache.clear()


def _to_schema(row: indicators_repo.IndicatorCatalogRow) -> IndicatorOut:
    return IndicatorOut(
        key=row.key,
        name=row.name,
        description=row.description,
        unit=row.unit,
        origin=row.origin,
        decimal_places=row.decimal_places,
        available_years=row.available_years,
        latest_year=row.latest_year,
    )


async def list_indicators(
    session: AsyncSession,
    *,
    level: TerritoryLevel | None = None,
) -> IndicatorListResponse:
    """Catálogo com cobertura temporal.

    `level` importa: a cobertura de um indicador pode diferir entre UF e
    município, e é a cobertura do nível exibido que deve popular o seletor de ano.
    """
    cached = _catalog_cache.get(level)
    if cached is None:
        rows = await indicators_repo.list_catalog(session, level=level)
        cached = [_to_schema(row) for row in rows]
        _catalog_cache.set(level, cached)
    return IndicatorListResponse(indicators=cached)


async def get_indicator(
    session: AsyncSession,
    key: str,
    *,
    level: TerritoryLevel | None = None,
) -> IndicatorOut:
    catalog = await list_indicators(session, level=level)
    for indicator in catalog.indicators:
        if indicator.key == key:
            return indicator
    raise IndicatorNotFoundError(key)
