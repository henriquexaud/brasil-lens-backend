"""Contrato público, fusos, ausência de dados e comportamento durante falhas externas."""

import asyncio
from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.errors import ProviderError, ProviderRateLimitedError
from app.providers import open_meteo
from app.schemas.weather import WeatherCity, WeatherCurrentResponse
from app.services import weather_forecast as service


def payload() -> dict:
    return {
        "timezone": "America/Rio_Branco",
        "current": {
            "time": datetime.now(UTC).timestamp(),
            "interval": 900,
            "temperature_2m": 28.4,
            "relative_humidity_2m": 0,
            "apparent_temperature": None,
            "precipitation": 0,
            "wind_speed_10m": 8.2,
            "weather_code": 0,
        },
        "daily": {
            "time": [1789794000, 1789880400, 1789966800],
            "weather_code": [0, 3, 61],
            "temperature_2m_min": [19, 20, None],
            "temperature_2m_max": [30, 31, 32],
            "precipitation_probability_max": [0, 80, None],
        },
    }


def city() -> WeatherCity:
    return open_meteo._parse_city(payload(), open_meteo.CAPITALS[0])


@pytest.fixture(autouse=True)
def clean_cache() -> Iterator[None]:
    service.reset_state()
    yield
    # Pausas e leituras são do processo: sem limpar ao sair, vazariam para os
    # testes de outros arquivos.
    service.reset_state()


def parsed(locations, *, include_forecast: bool = False, minutes_ago: float = 0):
    raw = payload()
    raw["current"]["time"] = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).timestamp()
    if not include_forecast:
        del raw["daily"]
    return [open_meteo._parse_city(raw, location) for location in locations]


def fake_fetch() -> AsyncMock:
    async def fetch(client, locations, *, include_forecast):
        return parsed(locations, include_forecast=include_forecast)

    return AsyncMock(side_effect=fetch)


def requested(fetch: AsyncMock) -> list[str]:
    return [location[1] for call in fetch.await_args_list for location in call.args[1]]


def store(point, *, minutes_ago: float, variant="current") -> service.WeatherReading:
    code, name, uf, lat, lon = point
    city = parsed(((uf, name, lat, lon),), minutes_ago=minutes_ago)[0]
    reading = service.WeatherReading(
        fetched_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        city=city.model_copy(update={"id": code}),
    )
    service._readings.set(f"{variant}:{code}", reading)
    return reading


async def settle() -> None:
    """Espera as renovações em segundo plano terminarem."""
    await asyncio.gather(*list(service._inflight.values()), return_exceptions=True)


SAO_PAULO = service._capital_point(next(c for c in open_meteo.CAPITALS if c[0] == "SP"))
MANAUS = service._capital_point(next(c for c in open_meteo.CAPITALS if c[0] == "AM"))


async def test_batch_preserves_locations_units_nulls_zero_and_local_dates() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert len(request.url.params["latitude"].split(",")) == 27
        assert request.url.params["wind_speed_unit"] == "kmh"
        assert request.url.params["timeformat"] == "unixtime"
        return httpx.Response(200, json=[payload() for _ in open_meteo.CAPITALS])

    async with httpx.AsyncClient(
        base_url="https://test", transport=httpx.MockTransport(respond)
    ) as client:
        cities = await open_meteo.fetch_locations(client)
    assert len(cities) == 27
    assert cities[0].name == "Rio Branco"
    assert cities[-1].name == "Palmas"
    assert cities[0].observed_at.utcoffset() == timedelta(0)
    assert cities[0].forecast[0].date.isoformat() == "2026-09-19"
    assert cities[0].apparent_temperature_c is None
    assert cities[0].humidity_pct == 0
    assert cities[0].precipitation_mm == 0
    assert cities[0].precipitation_interval_minutes == 15
    assert cities[0].forecast[-1].precipitation_probability_pct is None


async def test_single_location_response_is_an_object() -> None:
    async with httpx.AsyncClient(
        base_url="https://test",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload())),
    ) as client:
        cities = await open_meteo.fetch_locations(client, (("SP", "Santos", -23.96, -46.33),))
    assert cities[0].name == "Santos"
    assert cities[0].latitude == -23.96


@pytest.mark.parametrize("body", [[], {"error": True}, [None] * 27])
async def test_invalid_response_becomes_provider_error(body: object) -> None:
    async with httpx.AsyncClient(
        base_url="https://test",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
    ) as client:
        with pytest.raises(ProviderError):
            await open_meteo.fetch_locations(client)


async def test_missing_temperature_is_not_fabricated() -> None:
    raw = deepcopy(payload())
    raw["current"]["temperature_2m"] = None
    async with httpx.AsyncClient(
        base_url="https://test",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[raw] * 27)),
    ) as client:
        with pytest.raises(ProviderError):
            await open_meteo.fetch_locations(client)


@pytest.mark.parametrize(
    ("reason", "wait", "snippet"),
    [
        ("Minutely API request limit exceeded. Please try again in one minute.", 60, "pouco tempo"),
        ("Hourly API request limit exceeded. Please try again in the next hour.", 300, "por hora"),
        ("Daily API request limit exceeded. Please try again tomorrow.", 900, "diário"),
        ("Something new", 300, "excesso de requisições"),
    ],
)
async def test_provider_429_becomes_rate_limited_error(
    reason: str, wait: int, snippet: str
) -> None:
    body = {"error": True, "reason": reason}
    async with httpx.AsyncClient(
        base_url="https://test",
        transport=httpx.MockTransport(lambda _: httpx.Response(429, json=body)),
    ) as client:
        with pytest.raises(ProviderRateLimitedError) as caught:
            await open_meteo.fetch_locations(client)
    assert caught.value.retry_after_seconds == wait
    assert snippet in caught.value.message


async def test_provider_429_honors_retry_after_header() -> None:
    async with httpx.AsyncClient(
        base_url="https://test",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(429, text="não é json", headers={"Retry-After": "42"})
        ),
    ) as client:
        with pytest.raises(ProviderRateLimitedError) as caught:
            await open_meteo.fetch_locations(client)
    assert caught.value.retry_after_seconds == 42


async def test_rate_limited_endpoint_explains_the_cause_to_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import weather
    from app.main import app

    monkeypatch.setattr(
        weather,
        "get_current",
        AsyncMock(side_effect=ProviderRateLimitedError("Limite diário atingido.", 900)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/weather/current")
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "provider_rate_limited",
            "message": "Limite diário atingido.",
            "details": {"retryAfterSeconds": 900},
        }
    }


async def test_current_endpoint_contract_and_invalid_territory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import weather
    from app.main import app

    monkeypatch.setattr(
        weather,
        "get_current",
        AsyncMock(
            return_value=WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=[city()])
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/weather/current")
        assert response.status_code == 200
        assert response.json()["cities"][0]["precipitationIntervalMinutes"] == 15
        assert response.json()["source"] == "Open-Meteo"
        assert (await client.get("/api/v1/weather/current?territory=bad")).status_code == 422
        monkeypatch.setattr(
            weather, "get_current", AsyncMock(side_effect=ProviderError("Indisponível"))
        )
        failure = await client.get("/api/v1/weather/current")
        assert failure.status_code == 502
        assert failure.json()["error"]["code"] == "provider_error"


async def test_current_only_does_not_request_daily_forecast() -> None:
    raw = payload()
    del raw["daily"]

    def respond(request: httpx.Request) -> httpx.Response:
        # Sem previsão, só o total e a probabilidade de hoje — e a chuva horária de 24 h.
        assert request.url.params["daily"] == "precipitation_probability_max,precipitation_sum"
        assert request.url.params["forecast_days"] == "1"
        assert request.url.params["hourly"] == "precipitation"
        assert request.url.params["past_hours"] == "24"
        return httpx.Response(200, json=raw)

    async with httpx.AsyncClient(
        base_url="https://test", transport=httpx.MockTransport(respond)
    ) as client:
        result = await open_meteo.fetch_locations(
            client, (open_meteo.CAPITALS[0],), include_forecast=False
        )
    assert result[0].temperature_c == 28.4
    assert result[0].forecast == []


async def test_batch_endpoint_limits_and_required_scope() -> None:
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for query in ["", "?parent=3509502", "?parent=35&limit=1000", "?parent=35&offset=-1"]:
            assert (await client.get(f"/api/v1/weather/municipalities{query}")).status_code == 422


# ------------------------------------------------ leituras por município --


async def test_concurrent_requests_share_one_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = fake_fetch()
    monkeypatch.setattr(service, "fetch_locations", fetch)
    results = await asyncio.gather(*(service.get_current() for _ in range(8)))
    assert fetch.await_count == 1
    assert all(result.status == "ok" for result in results)
    assert isinstance(
        results[0].model_dump(mode="json", by_alias=True)["cities"][0]["temperatureC"], float
    )


async def test_overlapping_requests_fetch_each_municipality_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()

    async def fetch(client, locations, *, include_forecast):
        await release.wait()
        return parsed(locations)

    mocked = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(service, "fetch_locations", mocked)
    first = asyncio.create_task(service._resolve([SAO_PAULO, MANAUS], service.MAP_FRESHNESS))
    await asyncio.sleep(0)
    second = asyncio.create_task(service._resolve([MANAUS], service.MAP_FRESHNESS))
    await asyncio.sleep(0)
    release.set()
    (a, _), (b, _) = await asyncio.gather(first, second)
    assert requested(mocked) == ["São Paulo", "Manaus"]
    assert a[MANAUS[0]] is b[MANAUS[0]]


async def test_capital_reading_is_reused_by_every_route(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = fake_fetch()
    monkeypatch.setattr(service, "fetch_locations", fetch)
    everything = await service.get_current(include_forecast=False)
    assert len(everything.cities) == 27
    assert ("SP", "São Paulo") in {(city.id, city.name) for city in everything.cities}
    assert fetch.await_count == 1, "as 27 capitais numa única chamada"
    await service.get_current(include_forecast=False)
    assert fetch.await_count == 1
    # A capital da UF é a mesma leitura do município (código IBGE).
    assert service._readings.get(f"current:{SAO_PAULO[0]}") is not None


async def test_fresh_reading_needs_no_fetch_and_expired_one_renews_in_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = fake_fetch()
    monkeypatch.setattr(service, "fetch_locations", fetch)
    old = store(SAO_PAULO, minutes_ago=20)

    # Camada do mapa: 30 minutos de validade.
    page = await service._capitals([SAO_PAULO], "current")
    assert page.status == "ok" and page.fetched_at == old.fetched_at
    assert fetch.await_count == 0

    # Cidade selecionada: 15 minutos. A leitura vencida sai na hora e é renovada depois.
    readings, outdated = await service._resolve([SAO_PAULO], service.SELECTED_FRESHNESS)
    assert readings[SAO_PAULO[0]] is old and not outdated
    await settle()
    assert fetch.await_count == 1
    assert service._readings.get(f"current:{SAO_PAULO[0]}").fetched_at > old.fetched_at


async def test_a_new_fetch_waits_at_least_two_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = fake_fetch()
    monkeypatch.setattr(service, "fetch_locations", fetch)
    reading = store(SAO_PAULO, minutes_ago=0)
    # A fonte ainda devolve o intervalo anterior: consultar de novo agora não traria nada.
    reading.city.observed_at = datetime.now(UTC) - timedelta(minutes=16)
    await service._resolve([SAO_PAULO], service.SELECTED_FRESHNESS)
    assert fetch.await_count == 0


async def test_forecast_fetch_also_serves_current_conditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = fake_fetch()
    monkeypatch.setattr(service, "fetch_locations", fetch)
    forecast, _ = await service._resolve([SAO_PAULO], service.SELECTED_FRESHNESS, "forecast")
    current, _ = await service._resolve([SAO_PAULO], service.SELECTED_FRESHNESS, "current")
    assert forecast[SAO_PAULO[0]].city.forecast
    assert current[SAO_PAULO[0]].city.forecast == []
    assert fetch.await_count == 1


async def test_foreground_point_does_not_wait_for_an_unrelated_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(client, locations, *, include_forecast):
        if locations[0][1] == "Background":
            entered.set()
            await release.wait()
        return parsed(locations)

    monkeypatch.setattr(service, "fetch_locations", fetch)
    background = asyncio.create_task(
        service._resolve([("1200013", "Background", "AC", -10, -67)], service.MAP_FRESHNESS)
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        selected, _ = await asyncio.wait_for(
            service._resolve([SAO_PAULO], service.SELECTED_FRESHNESS), timeout=1
        )
        assert SAO_PAULO[0] in selected
        assert not background.done()
    finally:
        release.set()
        await background


async def test_client_that_gives_up_does_not_cancel_the_shared_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()

    async def fetch(client, locations, *, include_forecast):
        await release.wait()
        return parsed(locations)

    monkeypatch.setattr(service, "fetch_locations", fetch)
    request = asyncio.create_task(service._resolve([SAO_PAULO], service.MAP_FRESHNESS))
    await asyncio.sleep(0)
    request.cancel()
    release.set()
    await settle()
    assert service._readings.get(f"current:{SAO_PAULO[0]}") is not None


async def test_outage_serves_last_reading_as_previous_data_and_pauses_the_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = AsyncMock(side_effect=ProviderError("Indisponível"))
    monkeypatch.setattr(service, "fetch_locations", fetch)
    previous = store(SAO_PAULO, minutes_ago=5 * 60)
    for _ in range(3):
        result = await service._capitals([SAO_PAULO], "current")
        assert result.status == "stale"
        assert result.fetched_at == previous.fetched_at
        assert result.cities[0].observed_at == previous.city.observed_at
    assert fetch.await_count == 1


async def test_outage_without_readings_raises_and_says_when_it_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = AsyncMock(side_effect=ProviderError("Indisponível"))
    monkeypatch.setattr(service, "fetch_locations", fetch)
    with pytest.raises(ProviderError):
        await service._capitals([SAO_PAULO], "current")
    with pytest.raises(ProviderError, match="Nova tentativa automática"):
        await service._capitals([SAO_PAULO], "current")
    assert fetch.await_count == 1


async def test_expired_reading_is_not_renewed_while_the_source_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = AsyncMock(side_effect=ProviderError("Indisponível"))
    monkeypatch.setattr(service, "fetch_locations", fetch)
    service.open_meteo_cooldown.trip()
    store(SAO_PAULO, minutes_ago=40)
    result = await service._capitals([SAO_PAULO], "current")
    assert result.status == "stale", "leitura vencida com a fonte fora do ar é dado anterior"
    assert fetch.await_count == 0


async def test_rate_limit_stops_all_outbound_calls_and_serves_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = AsyncMock(side_effect=ProviderRateLimitedError("Limite diário atingido.", 900))
    monkeypatch.setattr(service, "fetch_locations", fetch)
    store(SAO_PAULO, minutes_ago=3 * 60)

    result = await service._capitals([SAO_PAULO], "current")
    assert result.status == "stale"
    assert fetch.await_count == 1

    # Outros municípios, sem leitura: falham com a causa real e sem tocar a fonte.
    for point in (MANAUS, ("3548500", "Santos", "SP", -23.96, -46.33)):
        with pytest.raises(ProviderRateLimitedError) as caught:
            await service._resolve([point], service.MAP_FRESHNESS)
        assert 0 < caught.value.retry_after_seconds <= 900
        assert caught.value.message == "Limite diário atingido."
    assert fetch.await_count == 1


async def test_rate_limit_cooldown_expires_and_service_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = AsyncMock(side_effect=ProviderRateLimitedError("Limite diário atingido.", 900))
    monkeypatch.setattr(service, "fetch_locations", fetch)
    with pytest.raises(ProviderRateLimitedError):
        await service.get_current()

    service._rate_limited_until = 0.0  # a espera terminou
    fetch.side_effect = fake_fetch().side_effect
    result = await service.get_current()
    assert result.status == "ok"
    assert fetch.await_count == 2


async def test_rate_limit_hitting_the_cooldown_does_not_extend_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service, "fetch_locations", AsyncMock(side_effect=ProviderRateLimitedError("x", 900))
    )
    with pytest.raises(ProviderRateLimitedError):
        await service.get_current()
    until = service._rate_limited_until
    with pytest.raises(ProviderRateLimitedError):
        await service._resolve([MANAUS], service.MAP_FRESHNESS)
    assert service._rate_limited_until == until


async def test_readings_beyond_twelve_hours_are_never_presented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store(SAO_PAULO, minutes_ago=13 * 60)

    async def fetch(client, locations, *, include_forecast):
        return parsed(locations, minutes_ago=3 * 60)

    monkeypatch.setattr(service, "fetch_locations", fetch)
    with pytest.raises(ProviderError, match="condições recentes"):
        await service._capitals([SAO_PAULO], "current")


def test_cell_size_follows_zoom_and_visible_extent() -> None:
    assert service._cell_size(8, (-50, -26, -43, -21.5)) == 0.5
    assert service._cell_size(9, (-48.4, -24.7, -44.9, -22.4)) == 0.25
    assert service._cell_size(10, (-47.5, -24.1, -45.8, -23)) == 0.0
    # Tela enorme num zoom alto: continua amostrando.
    assert service._cell_size(12, (-50, -26, -40, -20)) == 0.5
    assert service._cell_size(8, (-60, -30, -40, -15)) == 1.0


# ------------------------------------------------------- com o banco --


@pytest.mark.db
async def test_weather_state_uses_its_capital(session, monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = fake_fetch()
    monkeypatch.setattr(service, "fetch_locations", fetch)
    result = await service.get_territory_current(session, "35", include_forecast=False)
    assert [(entry.id, entry.name) for entry in result.cities] == [("SP", "São Paulo")]
    assert fetch.await_args.args[1] == (("SP", "São Paulo", SAO_PAULO[3], SAO_PAULO[4]),)
    # A mesma leitura atende o município da capital.
    city = await service.get_territory_current(session, "3550308", include_forecast=False)
    assert city.cities[0].id == "3550308"
    assert fetch.await_count == 1


@pytest.mark.db
async def test_weather_municipality_uses_local_geometry(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.repositories import territories

    point = await territories.get_weather_point(session, "3509502")
    assert point is not None
    assert -23.2 < point[0] < -22.5 and -47.4 < point[1] < -46.7
    fetch = fake_fetch()
    monkeypatch.setattr(service, "fetch_locations", fetch)
    result = await service.get_territory_current(session, "3509502")
    assert result.cities[0].id == "3509502"
    assert fetch.await_args.args[1] == (("SP", "Campinas", *point),)


@pytest.mark.db
async def test_batches_are_paginated_and_seed_the_selection(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.repositories import territories

    expected = await territories.list_weather_points(session, "35", 0, 3)
    fetch = fake_fetch()
    monkeypatch.setattr(service, "fetch_locations", fetch)
    first = await service.get_municipalities_current(session, "35", 0, 2)
    second = await service.get_municipalities_current(session, "35", 2, 2)
    assert first.next_offset == 2
    assert second.next_offset == 4
    assert [city.id for city in first.cities + second.cities[:1]] == [p[0] for p in expected]
    selected = await service.get_territory_current(
        session, first.cities[0].id, include_forecast=False
    )
    assert selected.cities[0].temperature_c == first.cities[0].temperature_c
    assert fetch.await_count == 2
    assert all(not entry.forecast for entry in first.cities)


@pytest.mark.db
async def test_state_sample_is_reused_by_the_close_view(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch = fake_fetch()
    monkeypatch.setattr(service, "fetch_locations", fetch)
    state = await service.get_state_weather(session, "35")
    sample = {city.id for city in state.cities if not city.is_inferred}
    assert len(sample) == service.STATE_SAMPLE_SIZE
    assert len(state.cities) == 645
    assert fetch.await_count == 1

    # Zoom 8 perto da capital: uma leitura por célula de 0,5°, amostra reaproveitada.
    bbox = (-50.1, -25.75, -43.1, -21.35)
    close = await service.get_viewport_current(session, bbox, 8, parent="35")
    fetched = requested(fetch)[service.STATE_SAMPLE_SIZE :]
    measured = [city for city in close.cities if not city.is_inferred]
    assert len(close.cities) > 300
    assert len(fetched) < 80, "mede uma célula, não cada município"
    assert not set(fetched) & {city.name for city in state.cities if city.id in sample}
    assert sample & {city.id for city in measured}
    assert all(city.id.startswith("35") for city in close.cities)

    # Arrastar dentro das mesmas células não consulta de novo.
    calls = fetch.await_count
    await service.get_viewport_current(session, (-50.0, -25.7, -43.2, -21.4), 8, parent="35")
    assert fetch.await_count == calls


# ----------------------------------------------------------------- chuva --


def test_rain_is_the_last_24_hours_and_live_rain_comes_from_the_latest_interval() -> None:
    now = int(datetime.now(UTC).timestamp())
    raw = payload()
    raw["current"]["time"] = now
    raw["current"]["precipitation"] = 0
    raw["current"]["weather_code"] = 61
    raw["hourly"] = {
        "time": [now - 7200, now - 3600, now, now + 3600],
        "precipitation": [1.2, None, 0.4, 9.0],  # a hora futura não entra
    }
    city = open_meteo._parse_city(raw, open_meteo.CAPITALS[0])
    assert city.precipitation_24h_mm == 1.6
    assert city.raining_now, "código de chuva, mesmo com o intervalo zerado"
    raw["current"]["weather_code"] = 3
    assert not open_meteo._parse_city(raw, open_meteo.CAPITALS[0]).raining_now
    raw["current"]["precipitation"] = 0.2
    assert open_meteo._parse_city(raw, open_meteo.CAPITALS[0]).raining_now


# ------------------------------------------------------- mapa do Brasil --

# Código, nome, latitude, longitude, área (km²), temperatura, céu, chuva em 24 h, chovendo.
SP_STATE = [
    (SAO_PAULO[0], "São Paulo", -23.5, -46.6, 1_500, 18.0, 3, 0.0, False),
    ("3541406", "Presidente Prudente", -22.1, -51.4, 100_000, 30.0, 0, 0.0, False),
    ("3543402", "Ribeirão Preto", -21.2, -47.8, 60_000, 28.0, 0, 4.0, False),
    ("3548500", "Santos", -23.9, -46.3, 500, 20.0, 61, 12.0, True),
    # Fora da amostra: a área de cada um vai para o ponto medido mais próximo, a capital.
    ("3509502", "Campinas", -22.9, -47.1, 80_000, 0.0, 0, 0.0, False),
    ("3552205", "Sorocaba", -23.5, -47.5, 6_500, 0.0, 0, 0.0, False),
]


def mock_sao_paulo_state(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    from app.repositories import territories

    state = AsyncMock()
    state.ibge_code, state.name, state.abbreviation = "35", "São Paulo", "SP"
    monkeypatch.setattr(territories, "list_territories", AsyncMock(return_value=[state]))
    points = [row[:4] for row in SP_STATE]
    monkeypatch.setattr(territories, "list_weather_points", AsyncMock(return_value=points))
    areas = [{"ibge_code": row[0], "area_km2": row[4]} for row in SP_STATE]
    monkeypatch.setattr(service, "municipality_areas", AsyncMock(return_value=areas))
    fields = ("temperature_c", "weather_code", "precipitation_24h_mm", "raining_now")
    values = {row[1]: dict(zip(fields, row[5:], strict=True)) for row in SP_STATE}

    async def fetch(client, locations, *, include_forecast):
        return [city.model_copy(update=values[city.name]) for city in parsed(locations)]

    mocked = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(service, "fetch_locations", mocked)
    return mocked


def test_each_state_is_measured_by_the_size_of_its_territory() -> None:
    assert service._sample_size(1_559_000, 62) == service.NATIONAL_MAX_POINTS  # Amazonas
    assert service._sample_size(357_000, 79) == 6  # Mato Grosso do Sul
    assert service._sample_size(21_900, 75) == service.NATIONAL_MIN_POINTS  # Sergipe
    assert service._sample_size(5_760, 1) == 1  # Distrito Federal: um município só


async def test_brazil_state_is_the_area_weighted_average_of_dispersed_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = mock_sao_paulo_state(monkeypatch)
    await service._capitals([SAO_PAULO], "current")  # primeira etapa: só a capital
    result = await service.get_states_weather(AsyncMock())
    [sp] = result.cities
    assert (sp.id, sp.name) == ("SP", "São Paulo")
    # 248,5 mil km²: quatro pontos, a capital (já lida) e os mais afastados dela.
    assert requested(fetch)[1:] == ["Presidente Prudente", "Ribeirão Preto", "Santos"]
    # A capital representa também Campinas e Sorocaba (88 mil km²); Santos, só a sua área.
    assert sp.temperature_c == 25.2, "só a capital: 18 °C; média simples: 24 °C"
    assert sp.precipitation_24h_mm == 1.0, "a chuva de Santos não cobre o estado (média simples: 4)"
    assert sp.weather_code == 0, "o céu que cobre a maior área"
    assert sp.raining_now and (sp.sample_points, sp.raining_points) == (4, 1)
    assert (sp.latitude, sp.longitude) == SAO_PAULO[3:], "a pílula continua na capital"
    assert result.summary.max_rainfall == 1.0


async def test_brazil_state_falls_back_to_its_capital_while_the_source_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_sao_paulo_state(monkeypatch)
    await service._capitals([SAO_PAULO], "current")
    failing = AsyncMock(side_effect=ProviderError("Indisponível"))
    monkeypatch.setattr(service, "fetch_locations", failing)
    result = await service.get_states_weather(AsyncMock())
    [sp] = result.cities
    assert (sp.sample_points, sp.temperature_c) == (1, 18.0)
    assert result.status == "stale"


@pytest.mark.db
async def test_brazil_map_measures_every_state_reusing_the_capitals(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch = fake_fetch()
    monkeypatch.setattr(service, "fetch_locations", fetch)
    await service.get_current(include_forecast=False)
    result = await service.get_states_weather(session)
    points = {city.id: city.sample_points for city in result.cities}
    assert len(points) == 27
    assert (points["AM"], points["SE"], points["DF"]) == (8, 2, 1)
    # As 27 capitais já lidas não voltam à fonte: os demais pontos saem numa chamada.
    assert fetch.await_count == 2
    assert len(requested(fetch)) == sum(points.values()) <= 120


def test_rain_fields_keep_the_frontend_names() -> None:
    body = city().model_copy(update={"precipitation_24h_mm": 3.2}).model_dump(by_alias=True)
    assert body["precipitation24hMm"] == 3.2
    assert {"rainingNow", "rainingPoints", "samplePoints"} <= body.keys()
