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


def _reset_service_state() -> None:
    service._cache.clear()
    service._fallback.clear()
    service._failures.clear()
    service._request_locks.clear()
    service._rate_limited_until = 0.0
    service._rate_limit_error = None


@pytest.fixture(autouse=True)
def clean_cache() -> Iterator[None]:
    _reset_service_state()
    yield
    # O cooldown é global do processo: sem limpar ao sair, vazaria para os
    # testes de outros arquivos que chamam `get_current`.
    _reset_service_state()


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


async def test_concurrent_requests_share_one_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = AsyncMock(return_value=[city()])
    monkeypatch.setattr(service, "fetch_locations", fetch)
    results = await asyncio.gather(*(service.get_current() for _ in range(8)))
    assert fetch.await_count == 1
    assert all(result.status == "ok" for result in results)
    assert isinstance(
        results[0].model_dump(mode="json", by_alias=True)["cities"][0]["temperatureC"], float
    )


async def test_outage_returns_identified_fallback_and_limits_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = AsyncMock(side_effect=ProviderError("Indisponível"))
    monkeypatch.setattr(service, "fetch_locations", fetch)
    previous = WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=[city()])
    service._fallback.set("capitals", previous)
    for _ in range(3):
        result = await service.get_current()
        assert result.status == "stale"
        assert result.fetched_at == previous.fetched_at
        assert result.cities[0].observed_at == previous.cities[0].observed_at
    assert fetch.await_count == 1


async def test_outage_without_cache_does_not_return_empty_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = AsyncMock(side_effect=ProviderError("Indisponível"))
    monkeypatch.setattr(service, "fetch_locations", fetch)
    for _ in range(2):
        with pytest.raises(ProviderError):
            await service.get_current()
    assert fetch.await_count == 1


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


async def test_rate_limit_stops_all_outbound_calls_and_serves_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = AsyncMock(side_effect=ProviderRateLimitedError("Limite diário atingido.", 900))
    monkeypatch.setattr(service, "fetch_locations", fetch)
    previous = WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=[city()])
    service._fallback.set("capitals", previous)

    result = await service.get_current()
    assert result.status == "stale"
    assert fetch.await_count == 1

    # Outra chave, sem fallback: falha com a causa real e sem tocar a fonte —
    # `_failures` é por chave, mas a cota é da aplicação inteira.
    for key in ("municipalities:35:0:16", "municipalities:35:16:16", "viewport:a:b"):
        with pytest.raises(ProviderRateLimitedError) as caught:
            await service.get_current(key, (("SP", "Santos", -23.96, -46.33),))
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
    fetch.side_effect = None
    fetch.return_value = [city()]
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
        await service.get_current("outra", (("SP", "Santos", -23.96, -46.33),))
    assert service._rate_limited_until == until


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


async def test_expired_measurements_are_never_presented_as_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = city().model_copy(update={"observed_at": datetime.now(UTC) - timedelta(hours=3)})
    service._fallback.set(
        "capitals", WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=[old])
    )
    monkeypatch.setattr(service, "fetch_locations", AsyncMock(return_value=[old]))
    with pytest.raises(ProviderError):
        await service.get_current()


@pytest.mark.db
async def test_weather_state_uses_its_capital(session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import TerritoryLevel
    from app.repositories import territories

    state = await territories.get_by_code(session, "35")
    assert state and state.level == TerritoryLevel.STATE
    capital = open_meteo._parse_city(payload(), ("SP", "São Paulo", -23.55, -46.63))
    monkeypatch.setattr(
        service,
        "get_current",
        AsyncMock(
            return_value=WeatherCurrentResponse(fetched_at=datetime.now(UTC), cities=[capital])
        ),
    )
    result = await service.get_territory_current(session, "35")
    assert [entry.name for entry in result.cities] == ["São Paulo"]
    assert service.get_current.call_args.args[0] == "capital:SP"
    assert len(service.get_current.call_args.args[1]) == 1


@pytest.mark.db
async def test_weather_municipality_uses_local_geometry(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.repositories import territories

    point = await territories.get_weather_point(session, "3509502")
    assert point is not None
    assert -23.2 < point[0] < -22.5 and -47.4 < point[1] < -46.7
    fetch = AsyncMock(return_value=[city()])
    monkeypatch.setattr(service, "fetch_locations", fetch)
    result = await service.get_territory_current(session, "3509502")
    assert result.cities[0].id == "3509502"
    assert fetch.await_args.args[1] == (("SP", "Campinas", *point),)


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
        assert "daily" not in request.url.params
        assert request.url.params["forecast_days"] == "1"
        return httpx.Response(200, json=raw)

    async with httpx.AsyncClient(
        base_url="https://test", transport=httpx.MockTransport(respond)
    ) as client:
        result = await open_meteo.fetch_locations(
            client, (open_meteo.CAPITALS[0],), include_forecast=False
        )
    assert result[0].temperature_c == 28.4
    assert result[0].forecast == []


async def test_foreground_location_does_not_wait_for_background_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(client, locations, **kwargs):
        if locations[0][1] == "Background":
            entered.set()
            await release.wait()
        return [city()]

    monkeypatch.setattr(service, "fetch_locations", fetch)
    background = asyncio.create_task(
        service.get_current("background", (("AC", "Background", -10, -67),), include_forecast=False)
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        selected = await asyncio.wait_for(service.get_current("selected"), timeout=1)
        assert selected.status == "ok"
        assert not background.done()
    finally:
        release.set()
        await background


@pytest.mark.db
async def test_batches_are_paginated_and_seed_current_cache(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.repositories import territories

    expected = await territories.list_weather_points(session, "35", 0, 3)
    assert len(expected) == 3

    async def fetch(client, locations, *, include_forecast):
        assert include_forecast is False
        return [
            open_meteo._parse_city(payload(), location).model_copy(update={"forecast": []})
            for location in locations
        ]

    mocked = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(service, "fetch_locations", mocked)
    first = await service.get_municipalities_current(session, "35", 0, 2)
    second = await service.get_municipalities_current(session, "35", 2, 2)
    assert first.next_offset == 2
    assert second.next_offset == 4
    assert first.cities[0].id == expected[0][0]
    assert first.cities[1].id == expected[1][0]
    assert second.cities[0].id == expected[2][0]
    assert not set(c.id for c in first.cities) & set(c.id for c in second.cities)
    selected = await service.get_territory_current(
        session, first.cities[0].id, include_forecast=False
    )
    assert selected.cities[0].temperature_c == first.cities[0].temperature_c
    assert mocked.await_count == 2
    assert all(not entry.forecast for entry in first.cities)


async def test_batch_endpoint_limits_and_required_scope() -> None:
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for query in ["", "?parent=3509502", "?parent=35&limit=1000", "?parent=35&offset=-1"]:
            assert (await client.get(f"/api/v1/weather/municipalities{query}")).status_code == 422


async def test_capitals_load_in_small_batches_and_warm_selection(monkeypatch):
    async def fetch(client, locations, *, include_forecast):
        assert include_forecast is False
        return [open_meteo._parse_city(payload(), location) for location in locations]

    mocked = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(service, "fetch_locations", mocked)
    pages = [await service.get_capitals_current(offset, 6) for offset in range(0, 27, 6)]
    assert len(pages[0].cities) == 6
    assert {city.id for city in pages[0].cities} == {"SP", "AM", "BA", "DF", "RS", "PE"}
    assert [page.next_offset for page in pages] == [6, 12, 18, 24, None]
    assert len({city.id for page in pages for city in page.cities}) == 27
    selected = await service.get_current("capital:SP", (), include_forecast=False)
    assert selected.cities[0].id == "SP"
    assert mocked.await_count == 5, "selecionar uma UF já carregada não busca as 27 capitais"
