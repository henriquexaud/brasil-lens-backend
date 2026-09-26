"""Contratos de território."""

from __future__ import annotations

from pydantic import Field

from app.models import TerritoryLevel
from app.schemas.common import CamelModel, Pagination

# Extensão geográfica no formato do GeoJSON: [oeste, sul, leste, norte].
BoundingBox = tuple[float, float, float, float]


class TerritoryRef(CamelModel):
    """Referência mínima a outro território (pai, capital)."""

    ibge_code: str
    name: str
    level: TerritoryLevel | None = None


class TerritorySummary(CamelModel):
    ibge_code: str
    name: str
    level: TerritoryLevel
    abbreviation: str | None = None
    parent: TerritoryRef | None = None


class TerritoryListResponse(CamelModel):
    territories: list[TerritorySummary]
    pagination: Pagination


class TerritoryDetail(TerritorySummary):
    capital: TerritoryRef | None = None
    children_count: int
    children_level: TerritoryLevel | None = None
    bbox: BoundingBox | None = None


class LocationInput(CamelModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
