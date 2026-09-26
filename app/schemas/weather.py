"""Contratos das rotas de clima — GeoJSON, sem `level`/`parent`.

Mesma decisão de `schemas/map.py` (FeatureCollection válida com membros
estrangeiros, consumível direto pelo `<GeoJSON>` do react-leaflet), mas sem
o escopo da malha: cada estação ou alerta tem sua própria geometria.
"""

from __future__ import annotations

import enum
import math
from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, FiniteFloat, model_validator

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


class WeatherAlertCategory(str, enum.Enum):
    """Classificação comum entre fontes — o frontend decide layout por isto,
    não por `provider`. Hoje é 100% função da fonte (o INMET só emite
    fenômeno meteorológico; o CEMADEN só emite risco geo-hidrológico), mas
    fica no alerta, não só documentado em `provider`, porque é o dado que a
    UI realmente usa (ver `services/weather.py::_category_for`).
    """

    METEOROLOGICAL = "meteorological"
    GEO_HYDROLOGICAL = "geo_hydrological"


class WeatherAlertSeverityLevel(str, enum.Enum):
    """Tier visual comum em 4 níveis normalizados:
    Moderado, Alto, Muito alto e Extremo.
    Mantém aliases legados para compatibilidade.
    """

    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"
    EXTREME = "extreme"
    # Aliases legados
    POTENTIAL = "potential"
    DANGER = "danger"
    OTHER = "other"


class WeatherAlertProperties(CamelModel):
    provider: str
    category: WeatherAlertCategory
    event: str
    severity: str
    severity_level: WeatherAlertSeverityLevel
    color: str | None = None
    # Frase livre opcional da fonte além de `event` (ex.: "BLUMENAU/SC" do
    # CEMADEN, onde `event` sozinho não diz o município). `None` para fontes
    # onde `event`/`risks` já bastam — o INMET, hoje.
    description: str | None = None
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
    # Acumulado das últimas 24 h, o que o mapa de chuva pinta; `precipitation_mm`
    # é só o intervalo mais recente e `precipitation_sum_mm`, o total de hoje.
    precipitation_24h_mm: FiniteFloat | None = Field(default=None, alias="precipitation24hMm")
    # Chovendo no intervalo mais recente (precipitação ou código de chuva).
    raining_now: bool = False
    # Visão do Brasil: quantos pontos medidos compõem a média da UF e quantos
    # têm chuva agora.
    sample_points: int | None = None
    raining_points: int | None = None
    forecast: list[WeatherForecastDay]
    is_inferred: bool = False


class WeatherSummary(CamelModel):
    min_temperature: float | None = None
    max_temperature: float | None = None
    max_rainfall: float | None = None
    hottest: list[WeatherCity] = Field(default_factory=list)
    coldest: list[WeatherCity] = Field(default_factory=list)
    ranked_rainfall: list[WeatherCity] = Field(default_factory=list)


def build_weather_summary(cities: list[WeatherCity]) -> WeatherSummary:
    valid_temps = [
        c for c in cities if c.temperature_c is not None and math.isfinite(c.temperature_c)
    ]
    if not valid_temps:
        hottest: list[WeatherCity] = []
        coldest: list[WeatherCity] = []
        min_temp = None
        max_temp = None
    else:
        temps = [c.temperature_c for c in valid_temps]
        min_temp = min(temps)
        max_temp = max(temps)
        sorted_desc = sorted(valid_temps, key=lambda c: c.temperature_c, reverse=True)
        if len(valid_temps) == 1:
            hottest = [sorted_desc[0]]
            coldest = []
        elif len(valid_temps) == 2:
            hottest = [sorted_desc[0]]
            coldest = [sorted_desc[1]]
        else:
            max_per_group = min(3, len(valid_temps) // 2 or 1)
            hottest = sorted_desc[:max_per_group]
            hottest_ids = {c.id for c in hottest}
            sorted_asc = sorted(valid_temps, key=lambda c: c.temperature_c)
            coldest = [c for c in sorted_asc if c.id not in hottest_ids][:max_per_group]

    def _rain_val(c: WeatherCity) -> float:
        val = next(
            (
                v
                for v in (c.precipitation_24h_mm, c.precipitation_sum_mm, c.precipitation_mm)
                if v is not None
            ),
            None,
        )
        return float(val) if val is not None and math.isfinite(val) else 0.0

    valid_rain = [c for c in cities if _rain_val(c) > 0]
    ranked_rainfall = sorted(valid_rain, key=_rain_val, reverse=True)[:5]
    max_rainfall = _rain_val(ranked_rainfall[0]) if ranked_rainfall else None

    return WeatherSummary(
        min_temperature=min_temp,
        max_temperature=max_temp,
        max_rainfall=max_rainfall,
        hottest=hottest,
        coldest=coldest,
        ranked_rainfall=ranked_rainfall,
    )


class WeatherCurrentResponse(CamelModel):
    source: str = "Open-Meteo"
    source_url: str = "https://open-meteo.com/"
    fetched_at: datetime
    status: WeatherSourceStatusValue = WeatherSourceStatusValue.OK
    cities: list[WeatherCity]
    summary: WeatherSummary | None = None
    next_offset: int | None = None

    @model_validator(mode="after")
    def populate_summary(self) -> WeatherCurrentResponse:
        if self.summary is None and self.cities:
            self.summary = build_weather_summary(self.cities)
        return self
