from typing import Literal

from pydantic import Field

from app.models import IndicatorOrigin, TerritoryLevel
from app.schemas.common import ApiDecimal, CamelModel
from app.schemas.territory import TerritoryDetail


class IndicatorOut(CamelModel):
    key: str
    name: str
    description: str | None
    unit: str
    origin: IndicatorOrigin
    decimal_places: int
    available_years: list[int]
    latest_year: int | None
    supported_levels: list[TerritoryLevel]


class IndicatorListResponse(CamelModel):
    indicators: list[IndicatorOut]
    version: str


class MapIndicatorMeta(CamelModel):
    key: str
    name: str
    unit: str
    decimal_places: int
    year: int | None
    requested_year: str
    available_years: list[int] = Field(default_factory=list)


class MapStatistics(CamelModel):
    min: ApiDecimal
    max: ApiDecimal
    mean: ApiDecimal
    median: ApiDecimal
    count: int
    missing: int


class MapClassification(CamelModel):
    method: Literal["quantile"] = "quantile"
    scope: Literal["national"] = "national"
    min: ApiDecimal
    max: ApiDecimal
    classes: int
    breaks: list[ApiDecimal]


class MapValue(CamelModel):
    ibge_code: str
    value: ApiDecimal | None = None
    class_index: int | None = None


class MapValuesResponse(CamelModel):
    level: TerritoryLevel
    parent: str | None = None
    indicator: MapIndicatorMeta
    statistics: MapStatistics | None = None
    classification: MapClassification | None = None
    values: list[MapValue]
    version: str


class IndicatorValueOut(CamelModel):
    key: str
    name: str
    unit: str
    decimal_places: int
    origin: IndicatorOrigin
    value: ApiDecimal | None
    year: int | None
    source: str | None = None


class TerritoryOverview(TerritoryDetail):
    indicators: list[IndicatorValueOut]
