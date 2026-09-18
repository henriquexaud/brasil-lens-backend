"""Contratos do catálogo de indicadores e das séries históricas."""

from __future__ import annotations

from app.models import IndicatorOrigin
from app.schemas.common import ApiDecimal, CamelModel


class IndicatorOut(CamelModel):
    key: str
    name: str
    description: str | None
    unit: str
    origin: IndicatorOrigin
    # Quantas casas decimais exibir. Metadado de formatação, não de aparência.
    decimal_places: int
    # Anos com dado, em ordem crescente. É o que popula o seletor de ano —
    # sem isso o frontend teria de adivinhar a cobertura de cada indicador.
    available_years: list[int]
    latest_year: int | None


class IndicatorListResponse(CamelModel):
    indicators: list[IndicatorOut]


class SeriesPoint(CamelModel):
    year: int
    value: ApiDecimal


class IndicatorSeries(CamelModel):
    key: str
    name: str
    unit: str
    decimal_places: int
    origin: IndicatorOrigin
    source: str | None
    points: list[SeriesPoint]


class TerritorySeriesResponse(CamelModel):
    """Séries históricas de um território — base para gráficos temporais."""

    ibge_code: str
    name: str
    series: list[IndicatorSeries]
