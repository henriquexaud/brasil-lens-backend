"""Contratos das rotas de clima — GeoJSON, sem `level`/`parent`.

Mesma decisão de `schemas/map.py` (FeatureCollection válida com membros
estrangeiros, consumível direto pelo `<GeoJSON>` do react-leaflet), mas sem
`scope`/`indicator`/`classification`: uma estação ou um alerta não tem
recorte territorial nem classe de quantil — ver `docs/ARCHITECTURE.md`
(contexto Clima) para o porquê.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, FiniteFloat

from app.schemas.common import ApiDecimal, CamelModel


class WeatherStationProperties(CamelModel):
    provider: str
    external_code: str
    name: str
    station_type: str
    state_abbreviation: str | None = None
    observed_at: datetime
    temperature_c: ApiDecimal | None = None
    humidity_pct: ApiDecimal | None = None
    pressure_hpa: ApiDecimal | None = None
    precipitation_mm: ApiDecimal | None = None


class WeatherStationFeature(CamelModel):
    type: Literal["Feature"] = "Feature"
    # `{provider}:{externalCode}` — estável e único mesmo se dois providers
    # reusarem o mesmo formato de código por coincidência.
    id: str
    properties: WeatherStationProperties
    geometry: dict[str, Any]


class WeatherStationCollection(CamelModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[WeatherStationFeature]


class WeatherAlertProperties(CamelModel):
    provider: str
    event: str
    severity: str
    color: str | None = None
    onset: datetime
    expires: datetime
    affected_ibge_codes: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)


class WeatherAlertFeature(CamelModel):
    type: Literal["Feature"] = "Feature"
    id: str
    properties: WeatherAlertProperties
    geometry: dict[str, Any]


class WeatherAlertCollection(CamelModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[WeatherAlertFeature]


class WeatherSourceStatusValue(str, enum.Enum):
    """Frescor de uma fonte, derivado de `ingestion_runs` — não do dado em si.

    Distinto de "sem dado" (política já coberta em `schemas/map.py`): aqui a
    pergunta é "a fonte está respondendo dentro do esperado", não "há valor
    para este território".
    """

    OK = "ok"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class WeatherSourceStatus(CamelModel):
    key: str
    name: str
    last_updated_at: datetime | None = None
    status: WeatherSourceStatusValue
    update_frequency_seconds: int


class WeatherSourcesResponse(CamelModel):
    sources: list[WeatherSourceStatus]


class WeatherForecastDay(CamelModel):
    date: date
    weather_code: int | None
    temperature_min_c: FiniteFloat | None
    temperature_max_c: FiniteFloat | None
    precipitation_probability_pct: FiniteFloat | None
    precipitation_sum_mm: FiniteFloat | None = None


class WeatherCity(CamelModel):
    id: str
    name: str
    state_abbreviation: str
    latitude: float
    longitude: float
    timezone: str
    observed_at: datetime
    temperature_c: FiniteFloat
    apparent_temperature_c: FiniteFloat | None
    humidity_pct: FiniteFloat | None
    wind_speed_kmh: FiniteFloat | None
    precipitation_mm: FiniteFloat | None
    precipitation_sum_mm: FiniteFloat | None = None
    precipitation_probability_pct: FiniteFloat | None = None
    precipitation_interval_minutes: int
    weather_code: int | None
    forecast: list[WeatherForecastDay]


class WeatherCurrentResponse(CamelModel):
    source: str = "Open-Meteo"
    source_url: str = "https://open-meteo.com/"
    fetched_at: datetime
    status: WeatherSourceStatusValue = WeatherSourceStatusValue.OK
    cities: list[WeatherCity]
    next_offset: int | None = None
