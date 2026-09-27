from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.models import TerritoryLevel, WeatherStationType


@dataclass(frozen=True, slots=True)
class TerritoryRecord:
    ibge_code: str
    name: str
    level: TerritoryLevel
    parent_ibge_code: str | None
    abbreviation: str | None = None


@dataclass(frozen=True, slots=True)
class GeometryRecord:
    ibge_code: str
    geojson: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WeatherStationRecord:
    provider: str
    external_code: str
    name: str
    station_type: WeatherStationType
    latitude: float
    longitude: float
    state_abbreviation: str | None = None


@dataclass(frozen=True, slots=True)
class WeatherObservationRecord:
    provider: str
    external_code: str
    observed_at: datetime
    temperature_c: Decimal | None = None
    humidity_pct: Decimal | None = None
    pressure_hpa: Decimal | None = None
    precipitation_mm: Decimal | None = None


@dataclass(frozen=True, slots=True)
class WeatherAlertRecord:
    provider: str
    external_id: str
    event: str
    severity: str
    onset: datetime
    expires: datetime
    polygon_geojson: dict[str, Any]
    color: str | None = None
    description: str | None = None
    affected_ibge_codes: tuple[str, ...] = field(default_factory=tuple)
    risks: tuple[str, ...] = field(default_factory=tuple)
    instructions: tuple[str, ...] = field(default_factory=tuple)
