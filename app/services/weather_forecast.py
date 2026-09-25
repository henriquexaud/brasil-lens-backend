"""Clima sob demanda: uma leitura por município, compartilhada por todas as rotas.

A Open-Meteo conta cada coordenada como uma consulta — um lote de 20 municípios
gasta 20 da cota. Por isso a unidade de cache é a leitura de cada município,
não o lote: capitais, amostra do estado, lotes, área visível e seleção resolvem
os mesmos pontos em memória → Redis → fonte, e só o que falta vai à
Open-Meteo, numa chamada. A capital lida no mapa do Brasil é a mesma leitura
do município quando ele é selecionado; a média de cada UF no Brasil usa o
começo da amostra do estado, e a amostra é reaproveitada pelo zoom.

Frescor segue a cadência da fonte, cujas condições atuais mudam a cada 15
minutos: a cidade selecionada vale até o próximo intervalo; as camadas do mapa
valem 30 minutos, porque ali a estimativa já erra mais do que a variação em 15
minutos. Vencida, a leitura continua sendo servida enquanto é renovada em
segundo plano, por até duas horas. Com a fonte fora do ar ou sem cota, vale
até doze horas, marcada como dado anterior — melhor que um mapa vazio.

No zoom próximo a medição acompanha a escala (`_cell_size`): uma leitura por
célula da grade, estimando os vizinhos a partir dela, e todos os municípios só
quando a área visível é pequena.
"""

import asyncio
import math
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Literal, NamedTuple

import httpx
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis_cache
from app.core.cache import TTLCache
from app.core.cooldown import SourceCooldown
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
from app.repositories.boundaries import municipality_areas
from app.schemas.weather import (
    WeatherCity,
    WeatherCurrentResponse,
    WeatherSourceStatusValue,
)
from app.services.spatial_interpolation import interpolate_municipal_weather

# Código IBGE, nome, UF, latitude, longitude.
Point = tuple[str, str, str, float, float]
Variant = Literal["current", "forecast"]

OPEN_METEO_URL = "https://api.open-meteo.com"
SELECTED_FRESHNESS = timedelta(minutes=15)
MAP_FRESHNESS = timedelta(minutes=30)
# Depois de uma consulta, a próxima espera ao menos isto: se a fonte ainda não
# publicou o intervalo seguinte, não adianta perguntar de novo em seguida.
MIN_FRESHNESS_AFTER_FETCH = timedelta(minutes=2)
# Botão "Atualizar dados": abaixo disto, o clique é ignorado (o dado já é
# recente o bastante); a partir disto, força a busca na hora em vez de só
# agendar a renovação em segundo plano (ver `_resolve`).
FORCE_MIN_AGE = timedelta(minutes=5)
MAX_DATA_AGE = timedelta(hours=2)
MAX_FALLBACK_AGE = timedelta(hours=12)
FUTURE_TOLERANCE = timedelta(minutes=15)
READING_TTL_SECONDS = int(MAX_FALLBACK_AGE.total_seconds())
READINGS_NAMESPACE = "weather-reading"
# Municípios medidos de fato por estado; os demais são interpolados (IDW).
STATE_SAMPLE_SIZE = 20
# Mapa do Brasil: cada UF é medida em pontos espalhados pelo território, um a
# cada ~60 mil km², de 2 a 8 — o Amazonas pede mais leituras que Sergipe. São
# os primeiros da mesma amostra (a capital é o primeiro), reaproveitados
# quando a UF é aberta.
NATIONAL_KM2_PER_POINT = 60_000
NATIONAL_MIN_POINTS = 2
NATIONAL_MAX_POINTS = 8
STATE_RESPONSE_SECONDS = 60
# Coordenadas por chamada HTTP à fonte: a URL cresce com cada uma.
FETCH_CHUNK = 100

CAPITAL_IBGE_CODES = {
    "AC": "1200401",
    "AL": "2704302",
    "AM": "1302603",
    "AP": "1600303",
    "BA": "2927408",
    "CE": "2304400",
    "DF": "5300108",
    "ES": "3205309",
    "GO": "5208707",
    "MA": "2111300",
    "MG": "3106200",
    "MS": "5002704",
    "MT": "5103403",
    "PA": "1501402",
    "PB": "2507507",
    "PE": "2611606",
    "PI": "2211001",
    "PR": "4106902",
    "RJ": "3304557",
    "RN": "2408102",
    "RO": "1100205",
    "RR": "1400100",
    "RS": "4314902",
    "SC": "4205407",
    "SE": "2800308",
    "SP": "3550308",
    "TO": "1721000",
}


class WeatherReading(BaseModel):
    """Condições de um município e o instante em que foram consultadas."""

    fetched_at: datetime
    city: WeatherCity


class _NationalSample(NamedTuple):
    """Pontos medidos de uma UF no mapa do Brasil e a área (km²) de cada um."""

    abbreviation: str
    name: str
    points: list[Point]
    weights: list[float]


_readings: TTLCache[WeatherReading] = TTLCache(READING_TTL_SECONDS, 12_000)
_state_responses: TTLCache[WeatherCurrentResponse] = TTLCache(STATE_RESPONSE_SECONDS, 64)
# Consulta em curso por ponto: quem pede o mesmo município espera por ela.
_inflight: dict[tuple[Variant, str], asyncio.Task[dict[str, WeatherReading]]] = {}
# Célula → município medido, por (UF, tamanho da célula).
_representatives: dict[tuple[str, float], dict[tuple[int, int], str]] = {}
# Amostra e pesos de cada UF no mapa do Brasil, por sigla: dependem só da malha.
_national_samples: dict[str, _NationalSample] = {}
# Falha da fonte pausa as consultas por um minuto; ver app/core/cooldown.py.
open_meteo_cooldown = SourceCooldown("open_meteo", 60)
# Cota da fonte esgotada (HTTP 429): enquanto vale, nenhuma consulta sai. É
# global porque a cota é da aplicação (IP), e a espera vem da própria fonte.
_rate_limited_until = 0.0
_rate_limit_error: ProviderRateLimitedError | None = None
logger = get_logger(__name__)


def _rate_limit_cooldown() -> ProviderRateLimitedError | None:
    remaining = _rate_limited_until - time.monotonic()
    if remaining <= 0 or _rate_limit_error is None:
        return None
    return ProviderRateLimitedError(_rate_limit_error.message, math.ceil(remaining))


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


def _source_failing() -> bool:
    return _rate_limit_cooldown() is not None or open_meteo_cooldown.active


def _within(city: WeatherCity, now: datetime, max_age: timedelta) -> bool:
    return -FUTURE_TOLERANCE <= now - city.observed_at <= max_age


def _is_fresh(reading: WeatherReading, now: datetime, freshness: timedelta) -> bool:
    fresh_until = max(
        reading.city.observed_at + freshness, reading.fetched_at + MIN_FRESHNESS_AFTER_FETCH
    )
    return now < fresh_until and _within(reading.city, now, MAX_DATA_AGE)


def _key(variant: Variant, code: str) -> str:
    return f"{variant}:{code}"


async def _load(keys: list[str], now: datetime, freshness: timedelta) -> dict[str, WeatherReading]:
    """Memória primeiro; o Redis completa o que falta ou venceu (outro processo renovou)."""
    found = {key: reading for key in keys if (reading := _readings.get(key)) is not None}
    pending = [key for key in keys if key not in found or not _is_fresh(found[key], now, freshness)]
    shared = await redis_cache.read_many(READINGS_NAMESPACE, pending, WeatherReading)
    for key, reading in zip(pending, shared, strict=True):
        if reading and (key not in found or reading.fetched_at > found[key].fetched_at):
            found[key] = reading
            _readings.set(key, reading)
    return found


async def _save(readings: dict[str, WeatherReading]) -> None:
    for key, reading in readings.items():
        _readings.set(key, reading)
    await redis_cache.write_many(READINGS_NAMESPACE, readings, READING_TTL_SECONDS)


async def _fetch_and_store(points: list[Point], variant: Variant) -> dict[str, WeatherReading]:
    if (limited := _rate_limit_cooldown()) is not None:
        raise limited
    if open_meteo_cooldown.active:
        raise ProviderError(
            "A fonte de clima (Open-Meteo) não está respondendo. "
            f"Nova tentativa automática em {open_meteo_cooldown.remaining_seconds()} s."
        )
    try:
        cities: list[WeatherCity] = []
        async with httpx.AsyncClient(base_url=OPEN_METEO_URL, timeout=20.0) as client:
            for start in range(0, len(points), FETCH_CHUNK):
                chunk = points[start : start + FETCH_CHUNK]
                cities += await fetch_locations(
                    client,
                    tuple((uf, name, lat, lon) for _, name, uf, lat, lon in chunk),
                    include_forecast=variant == "forecast",
                )
        now = datetime.now(UTC)
        if not all(_within(city, now, MAX_DATA_AGE) for city in cities):
            raise ProviderError("A fonte de clima não retornou condições recentes.")
    except ProviderRateLimitedError as exc:
        _start_rate_limit_cooldown(exc)
        raise
    except ProviderError:
        open_meteo_cooldown.trip()
        logger.warning("weather.open_meteo_unavailable", exc_info=True)
        raise

    fetched: dict[str, WeatherReading] = {}
    stored: dict[str, WeatherReading] = {}
    for point, city in zip(points, cities, strict=True):
        reading = WeatherReading(fetched_at=now, city=city.model_copy(update={"id": point[0]}))
        fetched[point[0]] = reading
        stored[_key(variant, point[0])] = reading
        if variant == "forecast":
            # A previsão traz as condições atuais: a seleção seguinte não consulta de novo.
            stored[_key("current", point[0])] = WeatherReading(
                fetched_at=now, city=reading.city.model_copy(update={"forecast": []})
            )
    await _save(stored)
    return fetched


def _start_fetch(points: list[Point], variant: Variant) -> asyncio.Task[dict[str, WeatherReading]]:
    """Inicia uma consulta desacoplada de quem pediu: um cliente que desiste
    não cancela a leitura que outros esperam, e o resultado fica no cache."""
    task = asyncio.create_task(_fetch_and_store(points, variant))
    keys = [(variant, point[0]) for point in points]
    for key in keys:
        _inflight[key] = task

    def finished(done: asyncio.Task[dict[str, WeatherReading]]) -> None:
        for key in keys:
            if _inflight.get(key) is done:
                del _inflight[key]
        if not done.cancelled():
            done.exception()  # quem esperava já tratou; evita o aviso de exceção não lida

    task.add_done_callback(finished)
    return task


async def _fetch(
    points: list[Point], variant: Variant
) -> tuple[dict[str, WeatherReading], ProviderError | None]:
    """Consulta os pontos, reaproveitando os que outra requisição já está buscando."""
    waiting: dict[asyncio.Task[dict[str, WeatherReading]], list[str]] = {}
    new: list[Point] = []
    for point in points:
        if (task := _inflight.get((variant, point[0]))) is not None:
            waiting.setdefault(task, []).append(point[0])
        else:
            new.append(point)
    if new:
        waiting[_start_fetch(new, variant)] = [point[0] for point in new]
    results: dict[str, WeatherReading] = {}
    error: ProviderError | None = None
    for task, codes in waiting.items():
        try:
            batch = await asyncio.shield(task)
        except ProviderError as exc:
            error = error or exc
            continue
        results.update({code: batch[code] for code in codes if code in batch})
    return results, error


async def _resolve(
    points: list[Point],
    freshness: timedelta,
    variant: Variant = "current",
    *,
    force: bool = False,
) -> tuple[dict[str, WeatherReading], bool]:
    """Leituras dos pontos e se alguma delas é um dado anterior.

    Frescas saem do cache; vencidas (até duas horas) também, renovadas em
    segundo plano; ausentes são consultadas agora. Se a fonte falha, vale o
    último dado de até doze horas. Sem nenhuma leitura, a falha sobe com a
    causa real.

    `force` é só o botão "Atualizar dados": com a última busca há menos de
    `FORCE_MIN_AGE`, o pedido é ignorado (dado já recente, mesma resposta de
    sempre); passado isso, a leitura é buscada agora — não só agendada em
    segundo plano — para o próprio clique já devolver o dado novo.
    """
    now = datetime.now(UTC)
    known = await _load([_key(variant, point[0]) for point in points], now, freshness)
    usable: dict[str, WeatherReading] = {}
    fallback: dict[str, WeatherReading] = {}
    missing: list[Point] = []
    renew: list[Point] = []
    force_now: list[Point] = []
    for point in points:
        reading = known.get(_key(variant, point[0]))
        if reading and _within(reading.city, now, MAX_DATA_AGE):
            usable[point[0]] = reading
            if force and now - reading.fetched_at >= FORCE_MIN_AGE:
                force_now.append(point)
            elif not _is_fresh(reading, now, freshness):
                renew.append(point)
        else:
            missing.append(point)
            if reading and _within(reading.city, now, MAX_FALLBACK_AGE):
                fallback[point[0]] = reading

    outdated = False
    to_fetch_now = missing + force_now
    if to_fetch_now:
        fetched, error = await _fetch(to_fetch_now, variant)
        usable.update(fetched)
        if error is not None:
            outdated = True
            for code, reading in fallback.items():
                usable.setdefault(code, reading)
            if not usable:
                raise error
    renew = [
        point for point in renew if (variant, point[0]) not in _inflight and point[0] in usable
    ]
    if renew and not _source_failing():
        _start_fetch(renew, variant)
    elif renew:
        # A fonte está falhando: a leitura vencida é o dado anterior possível.
        outdated = True
    return usable, outdated


async def _peek(points: list[Point]) -> dict[str, WeatherReading]:
    """Leituras já guardadas destes pontos, sem consultar a fonte."""
    now = datetime.now(UTC)
    known = await _load([_key("current", point[0]) for point in points], now, MAP_FRESHNESS)
    result: dict[str, WeatherReading] = {}
    for point in points:
        reading = known.get(_key("current", point[0]))
        if reading and _within(reading.city, now, MAX_DATA_AGE):
            result[point[0]] = reading
    return result


def _response(
    cities: list[WeatherCity],
    readings: Iterable[WeatherReading],
    outdated: bool,
    next_offset: int | None = None,
) -> WeatherCurrentResponse:
    # A resposta é tão recente quanto a leitura mais antiga que a compõe.
    fetched_at = min((reading.fetched_at for reading in readings), default=datetime.now(UTC))
    return WeatherCurrentResponse(
        fetched_at=fetched_at,
        status=WeatherSourceStatusValue.STALE if outdated else WeatherSourceStatusValue.OK,
        cities=cities,
        next_offset=next_offset,
    )


def _capital_point(capital: tuple[str, str, float, float]) -> Point:
    state, name, latitude, longitude = capital
    return (CAPITAL_IBGE_CODES[state], name, state, latitude, longitude)


async def _capitals(
    points: list[Point], variant: Variant, *, force: bool = False
) -> WeatherCurrentResponse:
    readings, outdated = await _resolve(points, MAP_FRESHNESS, variant, force=force)
    # No mapa do Brasil a capital representa a UF e é identificada pela sigla.
    found = [point for point in points if point[0] in readings]
    cities = [
        readings[point[0]].city.model_copy(update={"id": point[2], "name": point[1]})
        for point in found
    ]
    return _response(cities, (readings[point[0]] for point in found), outdated)


async def get_current(
    *, include_forecast: bool = True, force: bool = False
) -> WeatherCurrentResponse:
    """As 27 capitais de uma vez: a primeira etapa do mapa do Brasil."""
    points = [_capital_point(capital) for capital in CAPITALS]
    return await _capitals(points, "forecast" if include_forecast else "current", force=force)


def _sample_size(area_km2: float, municipalities: int) -> int:
    """Pontos medidos de uma UF no mapa do Brasil, pela área do território."""
    wanted = round(area_km2 / NATIONAL_KM2_PER_POINT)
    return min(municipalities, max(NATIONAL_MIN_POINTS, min(NATIONAL_MAX_POINTS, wanted)))


def _area_weights(points: list[Point], sample: list[Point], area: dict[str, float]) -> list[float]:
    """Área (km²) que cada ponto medido representa: cada município soma a sua ao
    ponto medido mais próximo — os polígonos de Thiessen, o método clássico da
    chuva média de uma região, aqui sobre a malha municipal."""
    cos_lat = math.cos(math.radians(sum(point[3] for point in points) / len(points)))
    weights = [0.0] * len(sample)
    for code, _, _, lat, lon in points:
        distances = [(s[3] - lat) ** 2 + ((s[4] - lon) * cos_lat) ** 2 for s in sample]
        weights[distances.index(min(distances))] += area.get(code, 0.0)
    return weights


async def _national_sample(session: AsyncSession) -> list[_NationalSample]:
    """Pontos e pesos de cada UF, calculados uma vez: a malha não muda entre consultas."""
    if not _national_samples:
        area = {
            row["ibge_code"]: row["area_km2"] or 0.0 for row in await municipality_areas(session)
        }
        states = await territories.list_territories(session, level=TerritoryLevel.STATE, limit=100)
        samples: dict[str, _NationalSample] = {}
        for state in states:
            abbreviation = state.abbreviation or ""
            rows = await territories.list_weather_points(session, state.ibge_code, 0, 10_000)
            points = [(code, name, abbreviation, lat, lon) for code, name, lat, lon in rows]
            if not points or abbreviation not in CAPITAL_IBGE_CODES:
                continue
            size = _sample_size(sum(area.get(point[0], 0.0) for point in points), len(points))
            sample = points[:size]
            samples[abbreviation] = _NationalSample(
                abbreviation, state.name, sample, _area_weights(points, sample, area)
            )
        # De uma vez: uma consulta concorrente nunca vê só parte das UFs.
        _national_samples.update(samples)
    return list(_national_samples.values())


def _weighted_mean(pairs: list[tuple[float, float]]) -> float | None:
    """Média ponderada; sem área conhecida (peso zero), a média simples."""
    if not pairs:
        return None
    total = sum(weight for weight, _ in pairs)
    if total <= 0:
        return round(sum(value for _, value in pairs) / len(pairs), 1)
    return round(sum(weight * value for weight, value in pairs) / total, 1)


def _state_average(
    sample: _NationalSample, readings: dict[str, WeatherReading]
) -> WeatherCity | None:
    """A UF como a média dos seus pontos, cada um pesando a área que representa.

    Temperatura, umidade, vento e chuva acumulada são médias; o céu é o que
    cobre a maior área; chance de chuva e "chovendo agora" valem para a UF se
    valem para algum ponto dela. Um ponto sem leitura sai da conta e os demais
    dividem o peso: sem nenhum além da capital, a UF volta a ser a capital.
    """
    measured = [
        (weight, readings[point[0]].city)
        for point, weight in zip(sample.points, sample.weights, strict=True)
        if point[0] in readings
    ]
    if not measured:
        return None
    cities = [city for _, city in measured]

    def mean(field: str) -> float | None:
        values = [(weight, getattr(city, field)) for weight, city in measured]
        return _weighted_mean([(weight, value) for weight, value in values if value is not None])

    # Área sob cada céu; no empate, o da capital (o primeiro ponto).
    sky: dict[int, float] = {}
    for weight, city in measured:
        if city.weather_code is not None:
            sky[city.weather_code] = sky.get(city.weather_code, 0.0) + weight
    chances = [
        city.precipitation_probability_pct
        for city in cities
        if city.precipitation_probability_pct is not None
    ]
    # A pílula continua na capital, onde a primeira etapa (só as capitais) a pôs.
    capital = readings.get(CAPITAL_IBGE_CODES[sample.abbreviation])
    return (capital.city if capital else cities[0]).model_copy(
        update={
            "id": sample.abbreviation,
            "name": sample.name,
            # Tão recente quanto o ponto mais antigo.
            "observed_at": min(city.observed_at for city in cities),
            "temperature_c": mean("temperature_c"),
            "apparent_temperature_c": mean("apparent_temperature_c"),
            "humidity_pct": mean("humidity_pct"),
            "wind_speed_kmh": mean("wind_speed_kmh"),
            "weather_code": max(sky, key=sky.__getitem__) if sky else None,
            "precipitation_24h_mm": mean("precipitation_24h_mm"),
            "precipitation_sum_mm": mean("precipitation_sum_mm"),
            "precipitation_mm": max((city.precipitation_mm or 0) for city in cities),
            "precipitation_probability_pct": max(chances) if chances else None,
            "raining_now": any(city.raining_now for city in cities),
            "sample_points": len(cities),
            "raining_points": sum(city.raining_now for city in cities),
            "forecast": [],
            "is_inferred": False,
        }
    )


async def get_states_weather(
    session: AsyncSession, *, force: bool = False
) -> WeatherCurrentResponse:
    """Cada UF no mapa do Brasil: a média dos seus pontos, ponderada pela área.

    A capital sozinha daria a todo o Amazonas o clima de Manaus, e chuva é
    local: um ponto seco diria "sem chuva" para o estado inteiro. Os pontos são
    os primeiros da amostra de dispersão da UF — a capital, já lida na primeira
    etapa do mapa, e os mais afastados dela —, que o clima do estado reaproveita.
    """
    samples = await _national_sample(session)
    readings, outdated = await _resolve(
        [point for sample in samples for point in sample.points], MAP_FRESHNESS, force=force
    )
    cities = [city for sample in samples if (city := _state_average(sample, readings))]
    return _response(cities, readings.values(), outdated)


async def get_territory_current(
    session: AsyncSession, code: str, *, include_forecast: bool = True, force: bool = False
) -> WeatherCurrentResponse:
    territory = await territories.get_by_code(session, code)
    if territory is None:
        raise TerritoryNotFoundError(code)
    variant: Variant = "forecast" if include_forecast else "current"
    if territory.level == TerritoryLevel.STATE:
        # Estado não tem um único clima: mostramos explicitamente sua capital.
        capital = next((c for c in CAPITALS if c[0] == territory.abbreviation), None)
        if capital is None:
            raise InvalidParameterError("Capital não encontrada para este estado.")
        point = _capital_point(capital)
        readings, outdated = await _resolve([point], SELECTED_FRESHNESS, variant, force=force)
        city = readings[point[0]].city.model_copy(update={"id": point[2], "name": point[1]})
        return _response([city], readings.values(), outdated)
    if territory.level != TerritoryLevel.MUNICIPALITY:
        raise InvalidParameterError("Selecione um estado ou município.", parameter="territory")
    location = await territories.get_weather_point(session, code)
    if location is None:
        raise ProviderError("A localização deste município ainda não está disponível.")
    parent = (
        await territories.get_by_code(session, territory.parent_ibge_code)
        if territory.parent_ibge_code
        else None
    )
    state = parent.abbreviation if parent and parent.abbreviation else ""
    point = (code, territory.name, state, *location)
    readings, outdated = await _resolve([point], SELECTED_FRESHNESS, variant, force=force)
    city = readings[code].city.model_copy(update={"id": code, "name": territory.name})
    return _response([city], readings.values(), outdated)


async def _state_points(session: AsyncSession, parent: str) -> tuple[str, list[Point]]:
    """UF e municípios do estado, já ordenados por dispersão espacial."""
    state = await territories.get_by_code(session, parent)
    if state is None:
        raise TerritoryNotFoundError(parent)
    if state.level != TerritoryLevel.STATE:
        raise InvalidParameterError("O recorte precisa ser um estado.", parameter="parent")
    abbreviation = state.abbreviation or ""
    rows = await territories.list_weather_points(session, parent, 0, 10_000)
    return abbreviation, [(code, name, abbreviation, lat, lon) for code, name, lat, lon in rows]


def _measured(point: Point, reading: WeatherReading) -> WeatherCity:
    return reading.city.model_copy(update={"id": point[0], "name": point[1], "is_inferred": False})


async def get_municipalities_current(
    session: AsyncSession,
    parent: str,
    offset: int,
    limit: int,
    *,
    force: bool = False,
) -> WeatherCurrentResponse:
    _, points = await _state_points(session, parent)
    page = points[offset : offset + limit]
    if not page:
        return WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=[])
    readings, outdated = await _resolve(page, MAP_FRESHNESS, force=force)
    cities = [_measured(point, readings[point[0]]) for point in page if point[0] in readings]
    return _response(
        cities,
        (readings[point[0]] for point in page if point[0] in readings),
        outdated,
        next_offset=offset + limit if offset + limit < len(points) else None,
    )


async def get_state_weather(
    session: AsyncSession, parent: str, *, force: bool = False
) -> WeatherCurrentResponse:
    abbreviation, points = await _state_points(session, parent)
    # O botão "Atualizar dados" não pode ser respondido por este cache de
    # instantes: ele não sabe se a leitura por trás já passou de `FORCE_MIN_AGE`.
    if not force and (cached := _state_responses.get(parent)) is not None:
        return cached
    if not points:
        return WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=[])
    # Os primeiros pontos da ordem de dispersão formam a amostra medida; o
    # restante é estimado a partir dela.
    sample = points[:STATE_SAMPLE_SIZE]
    readings, outdated = await _resolve(sample, MAP_FRESHNESS, force=force)
    measured = {
        point[0]: _measured(point, readings[point[0]]) for point in sample if point[0] in readings
    }
    base = list(measured.values())
    cities = [
        measured.get(code) or interpolate_municipal_weather(code, name, lat, lon, base, uf)
        for code, name, uf, lat, lon in points
    ]
    response = _response(cities, readings.values(), outdated)
    if not outdated:
        # Por instantes só: a validade real é a de cada leitura da amostra.
        _state_responses.set(parent, response)
    return response


_CELL_SIZES = (1.0, 0.5, 0.25, 0.1)
# Teto de medições por área visível: acima dele a grade engrossa, então quanto
# mais denso o recorte, menos municípios medidos (os demais são estimados).
MAX_MEASURED_PER_VIEW = 80


def _cell_size(zoom: int, bbox: tuple[float, float, float, float]) -> float:
    """Graus por célula medida: a densidade acompanha a escala; de perto, todos.

    A extensão da área também conta, para uma tela muito grande (ou um bbox
    montado à mão) não medir milhares de municípios num zoom alto.
    """
    span = max(bbox[2] - bbox[0], bbox[3] - bbox[1])
    if zoom >= 10 and span <= 3:
        return 0.0
    if zoom >= 9 and span <= 6:
        return 0.25
    return 0.5 if span <= 12 else 1.0


def _cell(latitude: float, longitude: float, size: float) -> tuple[int, int]:
    return (math.floor(latitude / size), math.floor(longitude / size))


def _cell_representatives(
    state: str, points: list[Point], size: float
) -> dict[tuple[int, int], str]:
    """Município medido de cada célula, estável entre usuários e movimentos do mapa.

    Preferência, nesta ordem: a amostra do estado (já medida ao abrir a UF), o
    medido da célula maior que contém esta (já medido no zoom anterior) e, na
    falta dos dois, o município mais próximo do centro da célula.
    """
    cache_key = (state, size)
    if (cached := _representatives.get(cache_key)) is not None:
        return cached
    sample = {point[0] for point in points[:STATE_SAMPLE_SIZE]}
    coarser = {
        code
        for larger in _CELL_SIZES
        if larger > size
        for code in _cell_representatives(state, points, larger).values()
    }
    best: dict[tuple[int, int], tuple[tuple[int, float, str], str]] = {}
    for code, _, _, lat, lon in points:
        cell = _cell(lat, lon, size)
        center_lat, center_lon = (cell[0] + 0.5) * size, (cell[1] + 0.5) * size
        priority = 0 if code in sample else 1 if code in coarser else 2
        rank = (priority, (lat - center_lat) ** 2 + (lon - center_lon) ** 2, code)
        if cell not in best or rank < best[cell][0]:
            best[cell] = (rank, code)
    representatives = {cell: code for cell, (_, code) in best.items()}
    _representatives[cache_key] = representatives
    return representatives


async def get_viewport_current(
    session: AsyncSession,
    bbox: tuple[float, float, float, float],
    zoom: int,
    parent: str | None = None,
    *,
    force: bool = False,
) -> WeatherCurrentResponse:
    visible = await viewport_repo.weather_points(session, bbox, parent=parent)
    if not visible:
        return WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=[])
    # Posições e amostra de cada UF da área (cache em processo): as mesmas
    # coordenadas do clima do estado, para a estimativa coincidir com a dele.
    abbreviations = {code[:2]: uf for code, _, uf, _, _ in visible}
    state_points: dict[str, list[Point]] = {}
    for state, uf in abbreviations.items():
        rows = await territories.list_weather_points(session, state, 0, 10_000)
        state_points[state] = [(code, name, uf, lat, lon) for code, name, lat, lon in rows]
    position = {point[0]: point for points in state_points.values() for point in points}
    area = [position.get(code, (code, name, uf, lat, lon)) for code, name, uf, lat, lon in visible]

    size = _cell_size(zoom, bbox)
    # Área densa: a grade engrossa até caber no teto de medições.
    for coarser in (value for value in sorted(_CELL_SIZES) if value > size):
        if len({_cell(p[3], p[4], size) for p in area} if size else area) <= MAX_MEASURED_PER_VIEW:
            break
        size = coarser
    if size:
        chosen: dict[str, Point] = {}
        for point in area:
            state = point[0][:2]
            cells = _cell_representatives(state, state_points.get(state, []), size)
            code = cells.get(_cell(point[3], point[4], size), point[0])
            chosen.setdefault(code, position.get(code, point))
        to_measure = list(chosen.values())
    else:
        to_measure = area
    readings, outdated = await _resolve(to_measure, MAP_FRESHNESS, force=force)
    # Amostra do estado e municípios já lidos por outras rotas entram de graça.
    sample = [point for points in state_points.values() for point in points[:STATE_SAMPLE_SIZE]]
    known = {**await _peek([p for p in sample + area if p[0] not in readings]), **readings}
    base = [reading.city for reading in known.values()]
    cities = [
        _measured(point, known[point[0]])
        if point[0] in known
        else interpolate_municipal_weather(point[0], point[1], point[3], point[4], base, point[2])
        for point in area
    ]
    return _response(cities, known.values(), outdated)


def reset_state() -> None:
    """Esquece leituras, consultas e pausas — usado pelos testes."""
    global _rate_limited_until, _rate_limit_error
    _readings.clear()
    _state_responses.clear()
    _representatives.clear()
    _national_samples.clear()
    _inflight.clear()
    open_meteo_cooldown.reset()
    _rate_limited_until = 0.0
    _rate_limit_error = None
