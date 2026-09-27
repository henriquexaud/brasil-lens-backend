from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.core.errors import ProviderError
from app.models import WeatherStationType
from app.providers.base import get_json
from app.providers.records import WeatherObservationRecord, WeatherStationRecord

SOURCE = "inmet_estacoes"
PROVIDER_KEY = "inmet"

_OPERATING_STATUS = "Operante"
_LOOKBACK_DAYS = 2
_MISSING_THRESHOLD = Decimal("-9990")

_RATE_LIMIT_MARKER = "limite de requisições"
_RATE_LIMIT_MAX_ATTEMPTS = 5
_RATE_LIMIT_BACKOFF_SECONDS = 4.0


async def _get_json_with_rate_limit_retry(client: httpx.AsyncClient, path: str) -> Any:
    for attempt in range(1, _RATE_LIMIT_MAX_ATTEMPTS + 1):
        try:
            return await get_json(client, path, source=SOURCE)
        except ProviderError as exc:
            body = str(exc.details.get("body") or "")
            if _RATE_LIMIT_MARKER not in body or attempt == _RATE_LIMIT_MAX_ATTEMPTS:
                raise
            await asyncio.sleep(_RATE_LIMIT_BACKOFF_SECONDS * attempt)
    raise AssertionError("inalcançável: o laço sempre retorna ou levanta")  # pragma: no cover


async def fetch_stations(client: httpx.AsyncClient) -> list[WeatherStationRecord]:
    raw = await get_json(client, "/estacoes/T", source=SOURCE)
    if not isinstance(raw, list):
        raise ProviderError(f"{SOURCE} devolveu formato inesperado para /estacoes/T.")

    records: list[WeatherStationRecord] = []
    for entry in raw:
        if not isinstance(entry, dict) or entry.get("CD_SITUACAO") != _OPERATING_STATUS:
            continue
        code = entry.get("CD_ESTACAO")
        latitude = _to_float(entry.get("VL_LATITUDE"))
        longitude = _to_float(entry.get("VL_LONGITUDE"))
        if not code or latitude is None or longitude is None:
            continue
        records.append(
            WeatherStationRecord(
                provider=PROVIDER_KEY,
                external_code=code,
                name=entry.get("DC_NOME") or code,
                station_type=WeatherStationType.AUTOMATIC_WEATHER,
                latitude=latitude,
                longitude=longitude,
                state_abbreviation=entry.get("SG_ESTADO"),
            )
        )
    return records


async def fetch_latest_observation(
    client: httpx.AsyncClient, station_code: str
) -> WeatherObservationRecord | None:
    end = datetime.now(UTC).date()
    start = end - timedelta(days=_LOOKBACK_DAYS)
    path = f"/estacao/{start.isoformat()}/{end.isoformat()}/{station_code}"
    try:
        raw = await _get_json_with_rate_limit_retry(client, path)
    except ProviderError as exc:
        if "HTTP 204" in str(exc):
            return None
        raise
    if not isinstance(raw, list) or not raw:
        return None

    observations = [
        obs
        for entry in raw
        if isinstance(entry, dict) and (obs := _to_observation(station_code, entry)) is not None
    ]
    if not observations:
        return None
    return max(observations, key=lambda obs: obs.observed_at)


def _to_observation(station_code: str, entry: dict[str, Any]) -> WeatherObservationRecord | None:
    observed_at = _parse_measured_at(entry.get("DT_MEDICAO"), entry.get("HR_MEDICAO"))
    if observed_at is None:
        return None
    return WeatherObservationRecord(
        provider=PROVIDER_KEY,
        external_code=station_code,
        observed_at=observed_at,
        temperature_c=_to_decimal(entry.get("TEM_INS")),
        humidity_pct=_to_decimal(entry.get("UMD_INS")),
        pressure_hpa=_to_decimal(entry.get("PRE_INS")),
        precipitation_mm=_to_decimal(entry.get("CHUVA")),
    )


def _parse_measured_at(raw_date: Any, raw_time: Any) -> datetime | None:
    if not raw_date or raw_time is None:
        return None
    try:
        day = date.fromisoformat(str(raw_date)[:10])
        padded = str(raw_time).strip().zfill(4)
        hour, minute = int(padded[:2]), int(padded[2:4])
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if parsed <= _MISSING_THRESHOLD:
        return None
    return parsed


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
