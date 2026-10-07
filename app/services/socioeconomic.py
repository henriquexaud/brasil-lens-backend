from dataclasses import asdict, dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.core.errors import InvalidParameterError, NotFoundError, TerritoryNotFoundError
from app.models import EXPECTED_PARENT_LEVEL, TerritoryLevel
from app.providers.ibge.agregados import LEVEL_BY_SIDRA
from app.providers.ibge.datasets import DERIVED_INDICATORS, SOURCED_INDICATORS
from app.repositories import socioeconomic as repository
from app.repositories import territories as territories_repository
from app.repositories.map_projection import data_version
from app.schemas.common import to_json
from app.schemas.socioeconomic import (
    IndicatorListResponse,
    IndicatorOut,
    IndicatorValueOut,
    MapClassification,
    MapIndicatorMeta,
    MapStatistics,
    MapValue,
    MapValuesResponse,
    TerritoryOverview,
)
from app.services.classification import ValueDistribution, describe
from app.services.territories import get_detail

_cache: TTLCache[bytes] = TTLCache(ttl_seconds=300, max_entries=32)
_overview_cache: TTLCache[bytes] = TTLCache(ttl_seconds=300, max_entries=64)


@dataclass(frozen=True, slots=True)
class ValueSnapshot:
    rows: list[tuple[str, str | None, Decimal | None]]
    distribution: ValueDistribution


_snapshots: TTLCache[ValueSnapshot] = TTLCache(ttl_seconds=300, max_entries=8)


def clear_cache() -> None:
    _cache.clear()
    _overview_cache.clear()
    _snapshots.clear()


def parse_year(year: str) -> int | None:
    if year == "latest":
        return None
    if not year.isascii() or not year.isdigit() or not 1900 <= int(year) <= 2100:
        raise InvalidParameterError(
            "Ano de referência inválido. Use 'latest' ou um ano entre 1900 e 2100.",
            parameter="year",
        )
    return int(year)


def supported_levels() -> dict[str, list[TerritoryLevel]]:
    levels: dict[str, set[TerritoryLevel]] = {}
    for spec in SOURCED_INDICATORS:
        levels.setdefault(spec.indicator_key, set()).update(
            LEVEL_BY_SIDRA[level] for level in spec.levels
        )
    for derived_spec in DERIVED_INDICATORS:
        levels[derived_spec.indicator_key] = set.intersection(
            *(levels[key] for key in derived_spec.dependencies)
        )
    return {key: sorted(value, key=lambda level: level.value) for key, value in levels.items()}


async def list_indicators(session: AsyncSession, level: TerritoryLevel) -> IndicatorListResponse:
    version = await repository.version(session)
    key = f"catalog:{level.value}:{version}"
    cached = _cache.get(key)
    if cached is not None:
        return IndicatorListResponse.model_validate_json(cached)
    levels = supported_levels()
    rows = await repository.catalog(session, level)
    result = IndicatorListResponse(
        version=version,
        indicators=[
            IndicatorOut(
                key=row["key"],
                name=row["name"],
                description=row["description"],
                unit=row["unit"],
                origin=row["origin"],
                decimal_places=row["decimal_places"],
                available_years=row["available_years"],
                latest_year=row["available_years"][-1] if row["available_years"] else None,
                supported_levels=levels[row["key"]],
            )
            for row in rows
        ],
    )
    _cache.set(key, to_json(result))
    return result


async def get_values(
    session: AsyncSession, *, level: TerritoryLevel, parent: str | None, indicator: str, year: str
) -> MapValuesResponse:
    requested_year = parse_year(year)
    if parent:
        if level is TerritoryLevel.COUNTRY:
            raise InvalidParameterError(
                "O nível 'country' não aceita um território pai.", parameter="parent"
            )
        identity = await territories_repository.get_identity_by_code(session, parent)
        if identity is None:
            raise TerritoryNotFoundError(parent)
        if identity[1] != EXPECTED_PARENT_LEVEL[level].value:
            raise InvalidParameterError(
                "O território pai não corresponde ao nível solicitado.", parameter="parent"
            )
    catalog = await list_indicators(session, level)
    selected = next((item for item in catalog.indicators if item.key == indicator), None)
    if selected is None:
        raise NotFoundError("Indicador socioeconômico não encontrado.", indicator=indicator)
    resolved_year = requested_year if requested_year is not None else selected.latest_year
    geography_version = await data_version(session)
    key = f"values:{catalog.version}:{geography_version}:{level.value}:{parent}:{indicator}:{year}"
    cached = _cache.get(key)
    if cached is not None:
        return MapValuesResponse.model_validate_json(cached)
    snapshot_key = (
        f"{catalog.version}:{geography_version}:{level.value}:{indicator}:{resolved_year}"
    )
    snapshot = _snapshots.get(snapshot_key)
    if snapshot is None:
        identifier = await repository.indicator_id(session, indicator)
        if identifier is None:
            raise NotFoundError("Indicador socioeconômico não encontrado.", indicator=indicator)
        rows = await repository.values(
            session, level=level, indicator_id=identifier, year=resolved_year
        )
        # A mesma distribuição serve o país e todas as UFs, inclusive latest e seu ano explícito.
        snapshot = ValueSnapshot(rows, describe([value for _, _, value in rows]))
        _snapshots.set(snapshot_key, snapshot)
    all_values, distribution = snapshot.rows, snapshot.distribution
    scoped_values = [item for item in all_values if parent is None or item[1] == parent]
    scoped_distribution = (
        distribution if parent is None else describe([value for _, _, value in scoped_values])
    )
    statistics = None
    classification = None
    if distribution.statistics and distribution.classification:
        classification = MapClassification(
            classes=distribution.classification.classes,
            breaks=distribution.classification.breaks,
            min=distribution.statistics.min,
            max=distribution.statistics.max,
        )
    if scoped_distribution.statistics:
        statistics = MapStatistics(**asdict(scoped_distribution.statistics))
    result = MapValuesResponse(
        level=level,
        parent=parent,
        version=f"{catalog.version}:{geography_version}",
        indicator=MapIndicatorMeta(
            key=selected.key,
            name=selected.name,
            unit=selected.unit,
            decimal_places=selected.decimal_places,
            year=resolved_year,
            requested_year=year,
            available_years=selected.available_years,
        ),
        statistics=statistics,
        classification=classification,
        values=[
            MapValue(ibge_code=code, value=value, class_index=distribution.class_index(value))
            for code, _, value in scoped_values
        ],
    )
    _cache.set(key, to_json(result))
    return result


async def get_overview(session: AsyncSession, code: str, year: str) -> TerritoryOverview:
    target_year = parse_year(year)
    version = await repository.version(session)
    geography_version = await data_version(session)
    key = f"overview:{version}:{geography_version}:{code}:{year}"
    cached = _overview_cache.get(key)
    if cached is not None:
        return TerritoryOverview.model_validate_json(cached)
    detail = await get_detail(session, code)
    catalog = await list_indicators(session, detail.level)
    values = await repository.territory_values(session, code, target_year)
    result = TerritoryOverview(
        **detail.model_dump(),
        indicators=[
            IndicatorValueOut(
                key=item.key,
                name=item.name,
                unit=item.unit,
                decimal_places=item.decimal_places,
                origin=item.origin,
                value=values.get(item.key, {}).get("value"),
                year=values.get(item.key, {}).get("year"),
                source=values.get(item.key, {}).get("source"),
            )
            for item in catalog.indicators
        ],
    )
    _overview_cache.set(key, to_json(result))
    return result
