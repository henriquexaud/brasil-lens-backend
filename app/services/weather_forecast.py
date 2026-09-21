"""Clima sob demanda, com cache limitado, deduplicação e fallback de até duas horas."""

import asyncio
from datetime import UTC, datetime, timedelta
from weakref import WeakValueDictionary

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis_cache
from app.core.cache import TTLCache
from app.core.errors import InvalidParameterError, ProviderError, TerritoryNotFoundError
from app.core.logging import get_logger
from app.models import TerritoryLevel
from app.providers.open_meteo import CAPITALS, fetch_locations
from app.repositories import territories
from app.schemas.weather import WeatherCurrentResponse, WeatherSourceStatusValue

CACHE_SECONDS = 600
MAX_DATA_AGE = timedelta(hours=2)
_cache: TTLCache[WeatherCurrentResponse] = TTLCache(CACHE_SECONDS, 128)
_fallback: TTLCache[WeatherCurrentResponse] = TTLCache(7200, 128)
_failures: TTLCache[bool] = TTLCache(60, 128)
_request_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
logger = get_logger(__name__)


def _usable(result: WeatherCurrentResponse | None) -> bool:
    return bool(
        result
        and result.cities
        and all(
            -timedelta(minutes=15) <= datetime.now(UTC) - city.observed_at <= MAX_DATA_AGE
            for city in result.cities
        )
    )


async def get_current(
    key: str = "capitals",
    locations: tuple[tuple[str, str, float, float], ...] = CAPITALS,
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
            _cache.set(key, shared, ttl_seconds=30)
            return shared
        try:
            if _failures.get(key):
                raise ProviderError("Clima temporariamente indisponível. Tente em um minuto.")
            async with httpx.AsyncClient(
                base_url="https://api.open-meteo.com", timeout=20.0
            ) as client:
                cities = await fetch_locations(client, locations, include_forecast=include_forecast)
            result = WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=cities)
            if not _usable(result):
                raise ProviderError("A fonte de clima não retornou condições recentes.")
            _cache.set(key, result)
            _fallback.set(key, result)
            await redis_cache.write("weather", key, result, CACHE_SECONDS)
            await redis_cache.write("weather-fallback", key, result, 7200)
            return result
        except ProviderError:
            # Não prolonga o cooldown em cada consulta de um cliente.
            if not _failures.get(key):
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
            await redis_cache.write("weather", key, entry, CACHE_SECONDS)
        _fallback.set(key, entry)
    return result.model_copy(
        update={"next_offset": offset + limit if offset + limit < len(ordered) else None}
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
    return result.model_copy(update={"cities": [result.cities[0].model_copy(update={"id": code})]})


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
        for city, point in zip(result.cities, points)
    ]
    # A seleção reaproveita as condições já aquecidas pelo lote, sem nova chamada externa.
    for city in cities:
        entry = result.model_copy(update={"cities": [city]})
        cache_key = f"{city.id}:current"
        if result.status == WeatherSourceStatusValue.OK:
            _cache.set(cache_key, entry)
            await redis_cache.write("weather", cache_key, entry, CACHE_SECONDS)
        _fallback.set(cache_key, entry)
    return result.model_copy(
        update={"cities": cities, "next_offset": offset + limit if more else None}
    )


async def get_viewport_current(
    session: AsyncSession,
    bbox: tuple[float, float, float, float],
    offset: int,
    limit: int,
    parent: str | None = None,
) -> WeatherCurrentResponse:
    from app.repositories.viewport import weather_points

    points = await weather_points(session, bbox, offset, limit + 1, parent=parent)
    more = len(points) > limit
    points = points[:limit]
    if not points:
        return WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=[])
    # Reaproveita por município, inclusive entre viewports e entre usuários.
    known = {}
    missing = []
    for code, name, uf, lat, lon in points:
        key = f"{code}:current"
        cached = _cache.get(key) or await redis_cache.read("weather", key, WeatherCurrentResponse)
        if cached and _usable(cached):
            known[code] = cached
        else:
            missing.append((code, name, uf, lat, lon))
    if missing:
        locations = tuple((uf, name, lat, lon) for _, name, uf, lat, lon in missing)
        key = "viewport:" + ":".join(item[0] for item in missing)
        result = await get_current(key, locations, include_forecast=False)
        for point, city in zip(missing, result.cities):
            code = point[0]
            entry = result.model_copy(update={"cities": [city.model_copy(update={"id": code})]})
            known[code] = entry
            if result.status == WeatherSourceStatusValue.OK:
                _cache.set(f"{code}:current", entry)
                await redis_cache.write("weather", f"{code}:current", entry, CACHE_SECONDS)
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
