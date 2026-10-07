from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.models import TerritoryLevel
from app.providers.base import get_json
from app.providers.ibge.indicator_records import IndicatorObservation
from app.providers.ibge.localidades import COUNTRY_CODE

logger = get_logger(__name__)

SOURCE = "ibge"
BASE_URL = "https://servicodados.ibge.gov.br/api/v3/agregados"


SidraLevel = Literal["N1", "N2", "N3", "N6"]

LEVEL_BY_SIDRA: dict[str, TerritoryLevel] = {
    "N1": TerritoryLevel.COUNTRY,
    "N2": TerritoryLevel.REGION,
    "N3": TerritoryLevel.STATE,
    "N6": TerritoryLevel.MUNICIPALITY,
}
SIDRA_BY_LEVEL: dict[TerritoryLevel, str] = {v: k for k, v in LEVEL_BY_SIDRA.items()}


UNAVAILABLE_TOKENS = frozenset({"...", "..", "-", "X", "", "*"})


_VALUE_SCALE = Decimal("0.000001")


class _Locality(BaseModel):
    id: str
    nome: str
    nivel: _Level


class _Level(BaseModel):
    id: str
    nome: str


class _Series(BaseModel):
    localidade: _Locality
    serie: dict[str, str]


class _Result(BaseModel):
    series: list[_Series]


class _Variable(BaseModel):
    id: str
    variavel: str
    unidade: str
    resultados: list[_Result]


_Locality.model_rebuild()


class AggregateQuery(BaseModel):
    table: str = Field(description="Código do agregado, ex. '4714'")
    variable: str = Field(
        description="Código da variável, ex. '93'. Várias ('6575|525') são somadas."
    )
    periods: str = Field(default="all", description="'all', '2022' ou '2021|2022'")

    classification: str | None = Field(default=None, description="Ex.: '1[1]' (Urbana)")

    @property
    def params(self) -> dict[str, str]:
        params = {}
        if self.classification:
            params["classificacao"] = self.classification
        return params


def territory_code(sidra_level: str, locality_id: str) -> str:
    if sidra_level == "N1":
        return COUNTRY_CODE
    return locality_id


def parse_value(raw: str, multiplier: Decimal) -> Decimal | None:
    token = raw.strip()
    if token in UNAVAILABLE_TOKENS:
        return None
    try:
        parsed = Decimal(token)
    except InvalidOperation:
        return None
    return parsed * multiplier if parsed.is_finite() else None


def reference_year(period: str) -> int | None:
    try:
        return int(period[:4])
    except ValueError:
        return None


def _annual_mean(values: list[Decimal]) -> Decimal:
    if len(values) == 1:
        return values[0]
    return (sum(values, Decimal(0)) / len(values)).quantize(_VALUE_SCALE)


def parse_observations(
    payload: list[dict[str, object]],
    *,
    multiplier: Decimal = Decimal(1),
    accept_levels: frozenset[str] | None = None,
) -> tuple[list[IndicatorObservation], int]:
    if not payload:
        raise ProviderError("IBGE Agregados devolveu lista vazia de variáveis.")

    discarded = 0

    parts: dict[tuple[str, str], list[Decimal]] = {}
    expected_parts = 0

    for raw_variable in payload:
        variable = _Variable.model_validate(raw_variable)
        for result in variable.resultados:
            expected_parts += 1
            for series in result.series:
                level_id = series.localidade.nivel.id
                if accept_levels is not None and level_id not in accept_levels:
                    continue
                code = territory_code(level_id, series.localidade.id)
                for period, raw_value in series.serie.items():
                    value = parse_value(raw_value, multiplier)
                    if value is None or reference_year(period) is None:
                        discarded += 1
                        continue
                    parts.setdefault((code, period), []).append(value)

    by_year: dict[tuple[str, int], list[Decimal]] = {}
    for (code, period), values in parts.items():
        if len(values) < expected_parts:
            discarded += len(values)
            continue
        year = reference_year(period)
        assert year is not None
        by_year.setdefault((code, year), []).append(sum(values, Decimal(0)))

    observations = [
        IndicatorObservation(
            ibge_code=code,
            reference_year=year,
            value=_annual_mean(values),
        )
        for (code, year), values in sorted(by_year.items())
    ]
    return observations, discarded


async def fetch_observations(
    client: httpx.AsyncClient,
    query: AggregateQuery,
    *,
    localities: str,
    multiplier: Decimal = Decimal(1),
    accept_levels: frozenset[str] | None = None,
) -> tuple[list[IndicatorObservation], int]:
    path = f"/api/v3/agregados/{query.table}/periodos/{query.periods}/variaveis/{query.variable}"
    payload = await get_json(
        client,
        path,
        params={"localidades": localities, **query.params},
        source="IBGE Agregados",
    )
    if not isinstance(payload, list):
        raise ProviderError("IBGE Agregados devolveu payload inesperado.", path=path)

    observations, discarded = parse_observations(
        payload,
        multiplier=multiplier,
        accept_levels=accept_levels,
    )
    logger.info(
        "agregados.fetched",
        extra={
            "table": query.table,
            "variable": query.variable,
            "localities": localities,
            "observations": len(observations),
            "discarded": discarded,
        },
    )
    return observations, discarded


async def fetch_available_periods(client: httpx.AsyncClient, table: str) -> list[str]:
    payload = await get_json(
        client,
        f"/api/v3/agregados/{table}/periodos",
        source="IBGE Agregados",
    )
    if not isinstance(payload, list):
        raise ProviderError("IBGE Agregados devolveu períodos inesperados.", table=table)
    return [str(item["id"]) for item in payload if isinstance(item, dict) and "id" in item]
