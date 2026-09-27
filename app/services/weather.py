from __future__ import annotations

from datetime import UTC, datetime

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.core.config import settings
from app.repositories import weather as weather_repo
from app.repositories.weather import SourceStatusRow
from app.schemas.weather import (
    WeatherAlertCategory,
    WeatherAlertCollection,
    WeatherAlertFeature,
    WeatherAlertProperties,
    WeatherAlertSeverityLevel,
    WeatherSourcesResponse,
    WeatherSourceStatus,
    WeatherSourceStatusValue,
    WeatherStationCollection,
    WeatherStationFeature,
    WeatherStationProperties,
)

_SOURCE_DEFINITIONS: tuple[tuple[str, str, str, str | None], ...] = (
    ("import_weather_inmet_alerts", "inmet_alerts", "INMET — Avisos Meteorológicos", None),
    (
        "import_weather_cemaden_alerts",
        "cemaden_alerts",
        "CEMADEN — Alertas de Risco Geo-Hidrológico",
        None,
    ),
)

_CATEGORY_BY_PROVIDER: dict[str, WeatherAlertCategory] = {
    "cemaden": WeatherAlertCategory.GEO_HYDROLOGICAL,
}


def _category_for(provider: str) -> WeatherAlertCategory:
    return _CATEGORY_BY_PROVIDER.get(provider, WeatherAlertCategory.METEOROLOGICAL)


def _severity_level_for(provider: str, severity: str) -> WeatherAlertSeverityLevel:
    text = (severity or "").strip().lower()
    if provider == "cemaden":
        if "muito alto" in text:
            return WeatherAlertSeverityLevel.VERY_HIGH
        if "extremo" in text:
            return WeatherAlertSeverityLevel.EXTREME
        if "alto" in text:
            return WeatherAlertSeverityLevel.HIGH
        if "moderado" in text:
            return WeatherAlertSeverityLevel.MODERATE
        return WeatherAlertSeverityLevel.MODERATE
    if "grande perigo" in text or "extremo" in text:
        return WeatherAlertSeverityLevel.EXTREME
    if "muito alto" in text:
        return WeatherAlertSeverityLevel.VERY_HIGH
    if "potencial" in text or "moderado" in text:
        return WeatherAlertSeverityLevel.MODERATE
    if "perigo" in text or "alto" in text:
        return WeatherAlertSeverityLevel.HIGH
    return WeatherAlertSeverityLevel.MODERATE


_STALE_MULTIPLIER = 3

_STATIONS_CACHE_KEY = "stations"
_ALERTS_CACHE_KEY = "alerts"
_stations_cache: TTLCache[WeatherStationCollection] = TTLCache(
    ttl_seconds=settings.weather_stations_cache_ttl_seconds, max_entries=1
)
_alerts_cache: TTLCache[WeatherAlertCollection] = TTLCache(
    ttl_seconds=settings.weather_alerts_cache_ttl_seconds, max_entries=1
)


async def get_stations(session: AsyncSession) -> WeatherStationCollection:
    cached = _stations_cache.get(_STATIONS_CACHE_KEY)
    if cached is not None:
        return cached

    rows = await weather_repo.list_current_stations(session)
    collection = WeatherStationCollection(
        features=[
            WeatherStationFeature(
                id=f"{row.provider}:{row.external_code}",
                properties=WeatherStationProperties(
                    provider=row.provider,
                    external_code=row.external_code,
                    name=row.name,
                    station_type=row.station_type,
                    state_abbreviation=row.state_abbreviation,
                    observed_at=row.observed_at,
                    temperature_c=row.temperature_c,
                    humidity_pct=row.humidity_pct,
                    pressure_hpa=row.pressure_hpa,
                    precipitation_mm=row.precipitation_mm,
                ),
                geometry=orjson.loads(row.geometry_json),
            )
            for row in rows
        ]
    )
    _stations_cache.set(_STATIONS_CACHE_KEY, collection)
    return collection


async def get_alerts(session: AsyncSession) -> WeatherAlertCollection:
    cached = _alerts_cache.get(_ALERTS_CACHE_KEY)
    if cached is not None:
        return cached

    rows = await weather_repo.list_active_alerts(session)
    collection = WeatherAlertCollection(
        features=[
            WeatherAlertFeature(
                id=f"{row.provider}:{row.external_id}",
                properties=WeatherAlertProperties(
                    provider=row.provider,
                    category=_category_for(row.provider),
                    event=row.event,
                    severity=row.severity,
                    severity_level=_severity_level_for(row.provider, row.severity),
                    color=row.color,
                    description=row.description,
                    onset=row.onset,
                    expires=row.expires,
                    affected_ibge_codes=row.affected_ibge_codes,
                    risks=row.risks,
                    instructions=row.instructions,
                ),
                geometry=orjson.loads(row.geometry_json),
            )
            for row in rows
        ]
    )
    _alerts_cache.set(_ALERTS_CACHE_KEY, collection)
    return collection


async def get_sources(session: AsyncSession) -> WeatherSourcesResponse:
    job_names = [job for job, _, _, _ in _SOURCE_DEFINITIONS]
    runs = await weather_repo.latest_run_per_job(session, job_names)

    frequency = settings.weather_refresh_interval_seconds
    sources = []
    for job, key, name, zero_output_key in _SOURCE_DEFINITIONS:
        status, last_updated_at = _status_for(
            runs.get(job), frequency_seconds=frequency, zero_output_key=zero_output_key
        )
        sources.append(
            WeatherSourceStatus(
                key=key,
                name=name,
                last_updated_at=last_updated_at,
                status=status,
                update_frequency_seconds=frequency,
            )
        )
    return WeatherSourcesResponse(sources=sources)


def _status_for(
    row: SourceStatusRow | None,
    *,
    frequency_seconds: int,
    zero_output_key: str | None,
) -> tuple[WeatherSourceStatusValue, datetime | None]:
    if row is None or row.finished_at is None or row.status == "failed":
        return WeatherSourceStatusValue.UNAVAILABLE, None

    if zero_output_key is not None and not (row.details or {}).get(zero_output_key):
        return WeatherSourceStatusValue.UNAVAILABLE, None

    elapsed_seconds = (datetime.now(UTC) - row.finished_at).total_seconds()
    if elapsed_seconds > frequency_seconds * _STALE_MULTIPLIER:
        return WeatherSourceStatusValue.STALE, row.finished_at
    return WeatherSourceStatusValue.OK, row.finished_at
