"""Acesso a dados do catálogo de indicadores e das séries de valores."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DataContext, Indicator, IndicatorOrigin, TerritoryLevel


@dataclass(frozen=True, slots=True)
class IndicatorCatalogRow:
    """Indicador + cobertura temporal observada nos dados."""

    key: str
    name: str
    description: str | None
    unit: str
    origin: IndicatorOrigin
    context: DataContext
    decimal_places: int
    display_order: int
    available_years: list[int]

    @property
    def latest_year(self) -> int | None:
        return self.available_years[-1] if self.available_years else None


@dataclass(frozen=True, slots=True)
class IndicatorValueRow:
    """Um valor com a proveniência resolvida, pronto para exibição."""

    indicator_key: str
    reference_year: int
    value: Decimal
    dataset_name: str
    dataset_source: str
    dataset_code: str


# ---------------------------------------------------------------------------
# Catálogo.
#
# Duas consultas de propósito, porque duas telas pedem coisas diferentes:
#
# * `list_definitions` — só os metadados do indicador (5 linhas). É o que o
#   overview precisa, e calcular cobertura ali custava 200 ms para devolver
#   1 KB.
# * `list_catalog` — metadados + cobertura temporal, que popula o seletor de
#   ano do mapa.
#
# A cobertura é extraída com um GROUP BY sobre o índice
# (indicator_id, reference_year) e só depois agregada em array. A forma
# aparentemente mais direta — ARRAY_AGG(DISTINCT ...) direto no join — força um
# sort de meio milhão de linhas com spill em disco (medido: 199 ms contra
# 56 ms). O GROUP BY aproveita a ordem do índice; o ARRAY_AGG externo opera
# sobre ~100 linhas.
#
# O recorte por ano fica no FILTER, não no WHERE: se estivesse no WHERE, um
# indicador sem nenhum valor no nível pedido desapareceria do catálogo em vez
# de aparecer com cobertura vazia — e o seletor de indicador perderia a opção.
#
# `context` é filtro adicional, no mesmo padrão de `key`: comparação por texto
# porque bind param de enum exigiria CAST explícito por dialect, e o valor já
# chega validado (é o `.value` de `DataContext`).
# ---------------------------------------------------------------------------
_DEFINITIONS_SQL = text(
    """
    SELECT i.key, i.name, i.description, i.unit, i.origin::text AS origin,
           i.context::text AS context, i.decimal_places, i.display_order
      FROM indicators i
     WHERE (CAST(:key AS text) IS NULL OR i.key = CAST(:key AS text))
       AND (CAST(:context AS text) IS NULL OR i.context::text = CAST(:context AS text))
     ORDER BY i.display_order, i.key
    """
)

_CATALOG_ANY_LEVEL_SQL = text(
    """
    WITH coverage AS (
        SELECT indicator_id, reference_year
          FROM indicator_values
         GROUP BY indicator_id, reference_year
    )
    SELECT i.key, i.name, i.description, i.unit, i.origin::text AS origin,
           i.context::text AS context, i.decimal_places, i.display_order,
           COALESCE(
               ARRAY_AGG(c.reference_year ORDER BY c.reference_year)
                   FILTER (WHERE c.reference_year IS NOT NULL),
               '{}'
           ) AS available_years
      FROM indicators i
      LEFT JOIN coverage c ON c.indicator_id = i.id
     WHERE (CAST(:key AS text) IS NULL OR i.key = CAST(:key AS text))
       AND (CAST(:context AS text) IS NULL OR i.context::text = CAST(:context AS text))
     GROUP BY i.id, i.key, i.name, i.description, i.unit, i.origin, i.context,
              i.decimal_places, i.display_order
     ORDER BY i.display_order, i.key
    """
)

_CATALOG_BY_LEVEL_SQL = text(
    """
    WITH coverage AS (
        SELECT v.indicator_id, v.reference_year
          FROM indicator_values v
          JOIN territories t ON t.id = v.territory_id
         WHERE t.level = CAST(:level AS territory_level)
         GROUP BY v.indicator_id, v.reference_year
    )
    SELECT i.key, i.name, i.description, i.unit, i.origin::text AS origin,
           i.context::text AS context, i.decimal_places, i.display_order,
           COALESCE(
               ARRAY_AGG(c.reference_year ORDER BY c.reference_year)
                   FILTER (WHERE c.reference_year IS NOT NULL),
               '{}'
           ) AS available_years
      FROM indicators i
      LEFT JOIN coverage c ON c.indicator_id = i.id
     WHERE (CAST(:key AS text) IS NULL OR i.key = CAST(:key AS text))
       AND (CAST(:context AS text) IS NULL OR i.context::text = CAST(:context AS text))
     GROUP BY i.id, i.key, i.name, i.description, i.unit, i.origin, i.context,
              i.decimal_places, i.display_order
     ORDER BY i.display_order, i.key
    """
)


def _to_catalog_row(row: object, *, years: list[int]) -> IndicatorCatalogRow:
    return IndicatorCatalogRow(
        key=row.key,  # type: ignore[attr-defined]
        name=row.name,  # type: ignore[attr-defined]
        description=row.description,  # type: ignore[attr-defined]
        unit=row.unit,  # type: ignore[attr-defined]
        origin=IndicatorOrigin(row.origin),  # type: ignore[attr-defined]
        context=DataContext(row.context),  # type: ignore[attr-defined]
        decimal_places=row.decimal_places,  # type: ignore[attr-defined]
        display_order=row.display_order,  # type: ignore[attr-defined]
        available_years=years,
    )


async def list_definitions(
    session: AsyncSession,
    *,
    key: str | None = None,
    context: DataContext | None = None,
) -> list[IndicatorCatalogRow]:
    """Apenas os metadados do indicador, sem cobertura temporal."""
    result = await session.execute(
        _DEFINITIONS_SQL, {"key": key, "context": context.value if context else None}
    )
    return [_to_catalog_row(row, years=[]) for row in result]


async def list_catalog(
    session: AsyncSession,
    *,
    level: TerritoryLevel | None = None,
    key: str | None = None,
    context: DataContext | None = None,
) -> list[IndicatorCatalogRow]:
    """Metadados + anos com dado. `level` restringe a cobertura ao nível exibido."""
    context_value = context.value if context else None
    if level is None:
        statement = _CATALOG_ANY_LEVEL_SQL
        params: dict[str, object] = {"key": key, "context": context_value}
    else:
        statement = _CATALOG_BY_LEVEL_SQL
        params = {"key": key, "context": context_value, "level": level.value}

    result = await session.execute(statement, params)
    return [_to_catalog_row(row, years=list(row.available_years or [])) for row in result]


async def get_indicator_id(session: AsyncSession, key: str) -> int | None:
    stmt = select(Indicator.id).where(Indicator.key == key)
    return (await session.execute(stmt)).scalar_one_or_none()


# DISTINCT ON resolve "latest por indicador" em uma passada, usando a PK
# (territory_id, indicator_id, reference_year) — sem subquery correlacionada.
# Quando :year é informado, o WHERE reduz a um único ano e o DISTINCT ON é inócuo.
_TERRITORY_VALUES_SQL = text(
    """
    SELECT DISTINCT ON (v.indicator_id)
           i.key              AS indicator_key,
           v.reference_year,
           v.value,
           d.name             AS dataset_name,
           d.source           AS dataset_source,
           d.code             AS dataset_code
      FROM indicator_values v
      JOIN indicators i ON i.id = v.indicator_id
      JOIN datasets   d ON d.id = v.dataset_id
      JOIN territories t ON t.id = v.territory_id
     WHERE t.ibge_code = :ibge_code
       AND (CAST(:year AS smallint) IS NULL
            OR v.reference_year = CAST(:year AS smallint))
     ORDER BY v.indicator_id, v.reference_year DESC
    """
)


async def latest_values_for_territory(
    session: AsyncSession,
    ibge_code: str,
    *,
    year: int | None = None,
) -> dict[str, IndicatorValueRow]:
    """Último valor disponível de cada indicador para um território.

    "Último" é por indicador: dois indicadores do mesmo território podem ter
    anos diferentes, e por isso cada valor devolvido carrega o seu próprio ano.
    """
    result = await session.execute(_TERRITORY_VALUES_SQL, {"ibge_code": ibge_code, "year": year})
    return {
        row.indicator_key: IndicatorValueRow(
            indicator_key=row.indicator_key,
            reference_year=row.reference_year,
            value=row.value,
            dataset_name=row.dataset_name,
            dataset_source=row.dataset_source,
            dataset_code=row.dataset_code,
        )
        for row in result
    }


# Série histórica completa: base para gráficos temporais no futuro.
_TERRITORY_SERIES_SQL = text(
    """
    SELECT i.key           AS indicator_key,
           i.name          AS indicator_name,
           i.unit,
           i.decimal_places,
           i.origin::text  AS origin,
           i.display_order,
           v.reference_year,
           v.value,
           d.name          AS dataset_name,
           d.source        AS dataset_source,
           d.code          AS dataset_code
      FROM indicator_values v
      JOIN indicators  i ON i.id = v.indicator_id
      JOIN datasets    d ON d.id = v.dataset_id
      JOIN territories t ON t.id = v.territory_id
     WHERE t.ibge_code = :ibge_code
       AND (CAST(:indicator_key AS text) IS NULL
            OR i.key = CAST(:indicator_key AS text))
       AND (CAST(:year_from AS smallint) IS NULL
            OR v.reference_year >= CAST(:year_from AS smallint))
       AND (CAST(:year_to AS smallint) IS NULL
            OR v.reference_year <= CAST(:year_to AS smallint))
     ORDER BY i.display_order, i.key, v.reference_year
    """
)


@dataclass(frozen=True, slots=True)
class SeriesPointRow:
    indicator_key: str
    indicator_name: str
    unit: str
    decimal_places: int
    origin: IndicatorOrigin
    reference_year: int
    value: Decimal
    dataset_name: str


async def series_for_territory(
    session: AsyncSession,
    ibge_code: str,
    *,
    indicator_key: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
) -> list[SeriesPointRow]:
    result = await session.execute(
        _TERRITORY_SERIES_SQL,
        {
            "ibge_code": ibge_code,
            "indicator_key": indicator_key,
            "year_from": year_from,
            "year_to": year_to,
        },
    )
    return [
        SeriesPointRow(
            indicator_key=row.indicator_key,
            indicator_name=row.indicator_name,
            unit=row.unit,
            decimal_places=row.decimal_places,
            origin=IndicatorOrigin(row.origin),
            reference_year=row.reference_year,
            value=row.value,
            dataset_name=row.dataset_name,
        )
        for row in result
    ]
