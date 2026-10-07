from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.results import affected_rows
from app.providers.specs import (
    DerivedIndicatorSpec,
    GrowthIndicatorSpec,
    RatioIndicatorSpec,
    ShareIndicatorSpec,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DerivationResult:
    indicator_key: str
    rows_written: int


_UPSERT_TAIL = """
    ON CONFLICT (territory_id, indicator_id, reference_year) DO UPDATE
       SET value            = EXCLUDED.value,
           dataset_id       = EXCLUDED.dataset_id,
           ingestion_run_id = EXCLUDED.ingestion_run_id,
           updated_at       = now()
     WHERE target.value IS DISTINCT FROM EXCLUDED.value
        OR target.dataset_id IS DISTINCT FROM EXCLUDED.dataset_id
"""

_INSERT_HEAD = """
    INSERT INTO socioeconomic_values AS target (
        territory_id, indicator_id, reference_year, value,
        dataset_id, ingestion_run_id, created_at, updated_at
    )
"""


_RATIO_SQL = text(
    _INSERT_HEAD
    + """
    SELECT numerator.territory_id,
           :target_indicator_id,
           numerator.reference_year,
           ROUND(numerator.value / denominator.value * :factor, 6),
           :dataset_id,
           :ingestion_run_id,
           now(),
           now()
      FROM socioeconomic_values numerator
      CROSS JOIN LATERAL (
          SELECT candidate.value
            FROM socioeconomic_values candidate
           WHERE candidate.territory_id = numerator.territory_id
             AND candidate.indicator_id = :denominator_indicator_id
             AND candidate.reference_year <= numerator.reference_year
           ORDER BY candidate.reference_year DESC
           LIMIT 1
      ) AS denominator
     WHERE numerator.indicator_id = :numerator_indicator_id
       AND denominator.value <> 0
    """
    + _UPSERT_TAIL
)


_GROWTH_SQL = text(
    _INSERT_HEAD
    + """
    SELECT observation.territory_id,
           :target_indicator_id,
           observation.reference_year,
           ROUND(
               (POWER(
                    observation.value / previous.value,
                    1.0 / (observation.reference_year - previous.reference_year)
                ) - 1) * :factor,
               6
           ),
           :dataset_id,
           :ingestion_run_id,
           now(),
           now()
      FROM socioeconomic_values observation
      CROSS JOIN LATERAL (
          SELECT candidate.value, candidate.reference_year
            FROM socioeconomic_values candidate
           WHERE candidate.territory_id = observation.territory_id
             AND candidate.indicator_id = :base_indicator_id
             AND candidate.reference_year < observation.reference_year
           ORDER BY candidate.reference_year DESC
           LIMIT 1
      ) AS previous
     WHERE observation.indicator_id = :base_indicator_id
       AND previous.value > 0
    """
    + _UPSERT_TAIL
)


_SHARE_SQL = text(
    _INSERT_HEAD
    + """
    SELECT part.territory_id,
           :target_indicator_id,
           part.reference_year,
           ROUND(part.value / national.value * :factor, 6),
           :dataset_id,
           :ingestion_run_id,
           now(),
           now()
      FROM socioeconomic_values part
      JOIN territories country
        ON country.level = CAST('country' AS territory_level)
      JOIN socioeconomic_values national
        ON national.territory_id  = country.id
       AND national.indicator_id  = :base_indicator_id
       AND national.reference_year = part.reference_year
     WHERE part.indicator_id = :base_indicator_id
       AND national.value <> 0
    """
    + _UPSERT_TAIL
)


async def derive(
    session: AsyncSession,
    spec: DerivedIndicatorSpec,
    *,
    indicator_ids: dict[str, int],
    dataset_id: int,
    ingestion_run_id: int | None = None,
) -> DerivationResult:
    parameters: dict[str, object] = {
        "target_indicator_id": indicator_ids[spec.indicator_key],
        "dataset_id": dataset_id,
        "ingestion_run_id": ingestion_run_id,
        "factor": spec.factor,
    }

    if isinstance(spec, RatioIndicatorSpec):
        statement = _RATIO_SQL
        parameters["numerator_indicator_id"] = indicator_ids[spec.numerator_key]
        parameters["denominator_indicator_id"] = indicator_ids[spec.denominator_key]
    elif isinstance(spec, GrowthIndicatorSpec):
        statement = _GROWTH_SQL
        parameters["base_indicator_id"] = indicator_ids[spec.base_key]
    elif isinstance(spec, ShareIndicatorSpec):
        statement = _SHARE_SQL
        parameters["base_indicator_id"] = indicator_ids[spec.base_key]
    else:
        raise TypeError(f"derivação não suportada: {type(spec).__name__}")

    result = await session.execute(statement, parameters)
    written = affected_rows(result)
    logger.info(
        "derived.computed",
        extra={
            "indicator": spec.indicator_key,
            "kind": type(spec).__name__,
            "depends_on": list(spec.dependencies),
            "rows_written": written,
        },
    )
    return DerivationResult(indicator_key=spec.indicator_key, rows_written=written)


_SCALE = Decimal("0.000001")


def expected_value(
    numerator: Decimal,
    denominator: Decimal,
    factor: Decimal = Decimal(1),
) -> Decimal:
    return (numerator / denominator * factor).quantize(_SCALE)


def expected_growth(
    value: Decimal,
    previous_value: Decimal,
    *,
    years: int,
    factor: Decimal = Decimal(100),
) -> Decimal:
    if years <= 0:
        raise ValueError("o intervalo entre os anos precisa ser positivo")
    ratio = value / previous_value
    annualized = ratio ** (Decimal(1) / Decimal(years))
    return ((annualized - 1) * factor).quantize(_SCALE)


def expected_share(
    value: Decimal,
    national_value: Decimal,
    factor: Decimal = Decimal(100),
) -> Decimal:
    return (value / national_value * factor).quantize(_SCALE)
