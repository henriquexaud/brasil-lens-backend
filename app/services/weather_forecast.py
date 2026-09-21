"""Clima sob demanda, com cache limitado, deduplicação e fallback de até duas horas."""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from math import ceil
from weakref import WeakValueDictionary

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis_cache
from app.core.cache import TTLCache
from app.core.errors import (
    InvalidParameterError,
    ProviderError,
    ProviderRateLimitedError,
    TerritoryNotFoundError,
)
from app.core.logging import get_logger
from app.models import TerritoryLevel
from app.providers.open_meteo import CAPITALS, fetch_locations
from app.repositories import territories
from app.repositories import viewport as viewport_repo
from app.schemas.weather import (
    WeatherCurrentResponse,
    WeatherSourceStatusValue,
    build_weather_summary,
)
from app.services.spatial_interpolation import interpolate_municipal_weather

Location = tuple[str, str, float, float]

OPEN_METEO_URL = "https://api.open-meteo.com"
CACHE_SECONDS = 600
MAX_DATA_AGE = timedelta(hours=2)
REDIS_TTL_SECONDS = 7200
# Municípios medidos de fato por estado; os demais são interpolados (IDW).
STATE_SAMPLE_SIZE = 20
_cache: TTLCache[WeatherCurrentResponse] = TTLCache(CACHE_SECONDS, 128)
_fallback: TTLCache[WeatherCurrentResponse] = TTLCache(7200, 128)
_failures: TTLCache[bool] = TTLCache(60, 128)
_request_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_revalidating: set[str] = set()
# O event loop guarda só referências fracas às tasks: sem este conjunto, uma
# revalidação em segundo plano pode ser coletada pelo GC no meio do caminho.
_background_tasks: set[asyncio.Task[None]] = set()
# Cota da fonte esgotada (HTTP 429): enquanto vale, nenhuma consulta sai. É
# global, e não por chave como `_failures`, porque a cota é da aplicação (IP):
# com um cooldown por chave, cada lote distinto tentaria a fonte de novo.
_rate_limited_until = 0.0
_rate_limit_error: ProviderRateLimitedError | None = None
logger = get_logger(__name__)


def _rate_limit_cooldown() -> ProviderRateLimitedError | None:
    remaining = _rate_limited_until - time.monotonic()
    if remaining <= 0 or _rate_limit_error is None:
        return None
    return ProviderRateLimitedError(_rate_limit_error.message, ceil(remaining))


def _start_rate_limit_cooldown(error: ProviderRateLimitedError) -> None:
    global _rate_limited_until, _rate_limit_error
    already_limited = time.monotonic() < _rate_limited_until
    _rate_limited_until = time.monotonic() + error.retry_after_seconds
    _rate_limit_error = error
    if not already_limited:
        logger.warning(
            "weather.open_meteo_rate_limited: %s (nova tentativa em %ss)",
            error.message,
            error.retry_after_seconds,
        )


def _usable(result: WeatherCurrentResponse | None) -> bool:
    return bool(
        result
        and result.cities
        and all(
            -timedelta(minutes=15) <= datetime.now(UTC) - city.observed_at <= MAX_DATA_AGE
            for city in result.cities
        )
    )


async def _fetch(locations: tuple[Location, ...], include_forecast: bool) -> WeatherCurrentResponse:
    async with httpx.AsyncClient(base_url=OPEN_METEO_URL, timeout=20.0) as client:
        cities = await fetch_locations(client, locations, include_forecast=include_forecast)
    return WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=cities)


async def _store(key: str, result: WeatherCurrentResponse) -> None:
    _cache.set(key, result)
    _fallback.set(key, result)
    await redis_cache.write("weather", key, result, REDIS_TTL_SECONDS)
    await redis_cache.write("weather-fallback", key, result, REDIS_TTL_SECONDS)


async def _background_revalidate(
    key: str,
    locations: tuple[Location, ...],
    include_forecast: bool,
) -> None:
    if key in _revalidating or _rate_limit_cooldown() is not None:
        return
    _revalidating.add(key)
    try:
        result = await _fetch(locations, include_forecast)
        if _usable(result):
            await _store(key, result)
    except ProviderRateLimitedError as exc:
        _start_rate_limit_cooldown(exc)
    except Exception:
        logger.warning("weather.revalidation_failed: %s", key, exc_info=True)
    finally:
        _revalidating.discard(key)


async def get_current(
    key: str = "capitals",
    locations: tuple[Location, ...] = CAPITALS,
    *,
    include_forecast: bool = True,
) -> WeatherCurrentResponse:
    key = key if include_forecast else f"{key}:current"
    # Cada lote tem sua trava; uma previsão selecionada nunca espera o estado inteiro.
    lock = _request_locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _cache.get(key)
        if cached and _usable(cached):
            return cached
        shared = await redis_cache.read("weather", key, WeatherCurrentResponse)
        if shared and _usable(shared):
            is_fresh = bool(
                shared.fetched_at
                and (datetime.now(UTC) - shared.fetched_at) <= timedelta(seconds=CACHE_SECONDS)
            )
            if is_fresh:
                _cache.set(key, shared, ttl_seconds=30)
                return shared
            # Stale-While-Revalidate: retorna imediatamente do cache e atualiza em segundo plano
            task = asyncio.create_task(_background_revalidate(key, locations, include_forecast))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
            return shared.model_copy(update={"status": WeatherSourceStatusValue.STALE})
        try:
            if (limited := _rate_limit_cooldown()) is not None:
                raise limited
            if _failures.get(key):
                raise ProviderError("Clima temporariamente indisponível. Tente em um minuto.")
            try:
                result = await _fetch(locations, include_forecast)
            except ProviderRateLimitedError as exc:
                _start_rate_limit_cooldown(exc)
                raise
            if not _usable(result):
                raise ProviderError("A fonte de clima não retornou condições recentes.")
            await _store(key, result)
            return result
        except ProviderError as exc:
            # Não prolonga o cooldown em cada consulta de um cliente. Cota
            # esgotada já tem cooldown global (e log próprio), sem traceback.
            if not isinstance(exc, ProviderRateLimitedError) and not _failures.get(key):
                _failures.set(key, True)
                logger.warning("weather.open_meteo_unavailable", exc_info=True)
            previous = _fallback.get(key) or await redis_cache.read(
                "weather-fallback", key, WeatherCurrentResponse
            )
            if previous and _usable(previous):
                return previous.model_copy(update={"status": WeatherSourceStatusValue.STALE})
            raise


async def get_capitals_current(offset: int, limit: int) -> WeatherCurrentResponse:
    # Primeiro lote cobre as cinco regiões; os demais completam as 27 UFs.
    first = ("SP", "AM", "BA", "DF", "RS", "PE")
    ordered = sorted(CAPITALS, key=lambda city: first.index(city[0]) if city[0] in first else 6)
    locations = tuple(ordered[offset : offset + limit])
    if not locations:
        return WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=[])
    result = await get_current(f"capitals:{offset}:{limit}", locations, include_forecast=False)
    for city in result.cities:
        entry = result.model_copy(update={"cities": [city]})
        key = f"capital:{city.id}:current"
        if result.status == WeatherSourceStatusValue.OK:
            _cache.set(key, entry)
            await redis_cache.write("weather", key, entry, REDIS_TTL_SECONDS)
        _fallback.set(key, entry)
    return result.model_copy(
        update={
            "summary": build_weather_summary(result.cities),
            "next_offset": offset + limit if offset + limit < len(ordered) else None,
        }
    )


async def get_territory_current(
    session: AsyncSession, code: str, *, include_forecast: bool = True
) -> WeatherCurrentResponse:
    territory = await territories.get_by_code(session, code)
    if territory is None:
        raise TerritoryNotFoundError(code)
    if territory.level == TerritoryLevel.STATE:
        # Estado não tem um único clima: mostramos explicitamente sua capital.
        capital = next((c for c in CAPITALS if c[0] == territory.abbreviation), None)
        if capital is None:
            raise InvalidParameterError("Capital não encontrada para este estado.")
        return await get_current(
            f"capital:{capital[0]}", (capital,), include_forecast=include_forecast
        )
    if territory.level != TerritoryLevel.MUNICIPALITY:
        raise InvalidParameterError("Selecione um estado ou município.", parameter="territory")
    point = await territories.get_weather_point(session, code)
    if point is None:
        raise ProviderError("A localização deste município ainda não está disponível.")
    parent = (
        await territories.get_by_code(session, territory.parent_ibge_code)
        if territory.parent_ibge_code
        else None
    )
    state = parent.abbreviation if parent and parent.abbreviation else ""
    result = await get_current(
        code, ((state, territory.name, *point),), include_forecast=include_forecast
    )
    cities = [result.cities[0].model_copy(update={"id": code})]
    return result.model_copy(
        update={
            "cities": cities,
            "summary": build_weather_summary(cities),
        }
    )


async def get_municipalities_current(
    session: AsyncSession,
    parent: str,
    offset: int,
    limit: int,
) -> WeatherCurrentResponse:
    state = await territories.get_by_code(session, parent)
    if state is None:
        raise TerritoryNotFoundError(parent)
    if state.level != TerritoryLevel.STATE:
        raise InvalidParameterError("O recorte precisa ser um estado.", parameter="parent")
    points = await territories.list_weather_points(session, parent, offset, limit + 1)
    more = len(points) > limit
    points = points[:limit]
    if not points:
        return WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=[])
    locations = tuple((state.abbreviation or "", name, lat, lon) for _, name, lat, lon in points)
    result = await get_current(
        f"municipalities:{parent}:{offset}:{limit}", locations, include_forecast=False
    )
    cities = [
        city.model_copy(update={"id": point[0]})
        for city, point in zip(result.cities, points, strict=False)
    ]
    # A seleção reaproveita as condições já aquecidas pelo lote, sem nova chamada externa.
    for city in cities:
        entry = result.model_copy(update={"cities": [city]})
        cache_key = f"{city.id}:current"
        if result.status == WeatherSourceStatusValue.OK:
            _cache.set(cache_key, entry)
            await redis_cache.write("weather", cache_key, entry, REDIS_TTL_SECONDS)
        _fallback.set(cache_key, entry)
    return result.model_copy(
        update={
            "cities": cities,
            "summary": build_weather_summary(cities),
            "next_offset": offset + limit if more else None,
        }
    )


async def get_viewport_current(
    session: AsyncSession,
    bbox: tuple[float, float, float, float],
    offset: int,
    limit: int,
    parent: str | None = None,
) -> WeatherCurrentResponse:
    points = await viewport_repo.weather_points(session, bbox, offset, limit + 1, parent=parent)
    more = len(points) > limit
    points = points[:limit]
    if not points:
        return WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=[])
    # Reaproveita por município, inclusive entre viewports e entre usuários.
    known = {}
    missing = []
    for code, name, uf, lat, lon in points:
        cached = _cache.get(f"{code}:current") or await redis_cache.read(
            "weather", f"{code}:current", WeatherCurrentResponse
        )
        if cached and _usable(cached):
            known[code] = cached
        else:
            missing.append((code, name, uf, lat, lon))
    if missing:
        locations = tuple((uf, name, lat, lon) for _, name, uf, lat, lon in missing)
        key = "viewport:" + ":".join(item[0] for item in missing)
        result = await get_current(key, locations, include_forecast=False)
        for point, city in zip(missing, result.cities, strict=False):
            code = point[0]
            entry = result.model_copy(update={"cities": [city.model_copy(update={"id": code})]})
            known[code] = entry
            if result.status == WeatherSourceStatusValue.OK:
                _cache.set(f"{code}:current", entry)
                await redis_cache.write("weather", f"{code}:current", entry, REDIS_TTL_SECONDS)
            _fallback.set(f"{code}:current", entry)
    values = [v for v in known.values() if v.cities]
    if not values:
        return WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=[])
    cities = [
        known[point[0]].cities[0].model_copy(update={"id": point[0]})
        for point in points
        if point[0] in known and known[point[0]].cities
    ]
    return WeatherCurrentResponse(
        fetched_at=min(item.fetched_at for item in values),
        status=WeatherSourceStatusValue.STALE
        if any(item.status == WeatherSourceStatusValue.STALE for item in values)
        else WeatherSourceStatusValue.OK,
        cities=cities,
        next_offset=offset + limit if more else None,
    )


async def get_state_weather(
    session: AsyncSession,
    parent: str,
) -> WeatherCurrentResponse:
    state = await territories.get_by_code(session, parent)
    if state is None:
        raise TerritoryNotFoundError(parent)
    if state.level != TerritoryLevel.STATE:
        raise InvalidParameterError("O recorte precisa ser um estado.", parameter="parent")

    cache_key = f"state-weather:{parent}"
    cached = _cache.get(cache_key)
    if cached and _usable(cached):
        return cached
    shared = await redis_cache.read("weather", cache_key, WeatherCurrentResponse)
    if shared and _usable(shared):
        _cache.set(cache_key, shared, ttl_seconds=60)
        return shared

    # Todos os municípios, já ordenados por dispersão espacial: os primeiros
    # formam a amostra medida e o restante é estimado a partir dela.
    points = await territories.list_weather_points(session, parent, 0, 10_000)
    if not points:
        return WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=[])
    sample = points[:STATE_SAMPLE_SIZE]
    state_abbr = state.abbreviation or ""
    raw_result = await get_current(
        f"state-sample:{parent}",
        tuple((state_abbr, name, lat, lon) for _, name, lat, lon in sample),
        include_forecast=False,
    )
    measured = {
        point[0]: city.model_copy(update={"id": point[0], "is_inferred": False})
        for city, point in zip(raw_result.cities, sample, strict=False)
    }
    measured_cities = list(measured.values())
    cities = [
        measured.get(code)
        or interpolate_municipal_weather(code, name, lat, lon, measured_cities, state_abbr)
        for code, name, lat, lon in points
    ]
    response = WeatherCurrentResponse(
        # A estimativa é tão recente quanto a amostra que a originou.
        fetched_at=raw_result.fetched_at,
        status=raw_result.status,
        cities=cities,
        summary=build_weather_summary(cities),
    )
    if response.status == WeatherSourceStatusValue.OK:
        _cache.set(cache_key, response)
        await redis_cache.write("weather", cache_key, response, REDIS_TTL_SECONDS)
    else:
        # Amostra vinda do fallback: guardada só por instantes, para o estado
        # voltar a ser medido assim que a fonte se recuperar — em vez de o
        # Redis servir o dado antigo por até duas horas.
        _cache.set(cache_key, response, ttl_seconds=60)
    return response
