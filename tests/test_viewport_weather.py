"""Municípios visíveis reutilizam dados atuais, inclusive entre UFs."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from app.core import redis_cache
from app.repositories import viewport
from app.schemas.weather import WeatherCity, WeatherCurrentResponse
from app.services import weather_forecast as service


async def test_viewport_only_fetches_missing_cities_and_warms_individual_cache(monkeypatch):
    service._cache.clear()
    points = [
        ("3550308", "São Paulo", "SP", -23.55, -46.63),
        ("3304557", "Rio de Janeiro", "RJ", -22.9, -43.2),
    ]
    monkeypatch.setattr(viewport, "weather_points", AsyncMock(return_value=points))
    monkeypatch.setattr(redis_cache, "read", AsyncMock(return_value=None))
    monkeypatch.setattr(redis_cache, "write", AsyncMock())

    def result(code, name, uf, lat, lon):
        return WeatherCurrentResponse(
            fetched_at=datetime.now(UTC),
            cities=[
                WeatherCity(
                    id=code,
                    name=name,
                    state_abbreviation=uf,
                    latitude=lat,
                    longitude=lon,
                    observed_at=datetime.now(UTC),
                    timezone="America/Sao_Paulo",
                    temperature_c=25,
                    apparent_temperature_c=None,
                    humidity_pct=None,
                    wind_speed_kmh=None,
                    precipitation_mm=None,
                    precipitation_interval_minutes=15,
                    weather_code=0,
                    forecast=[],
                )
            ],
        )

    service._cache.set("3550308:current", result(*points[0]))
    fetch = AsyncMock(return_value=result(*points[1]))
    monkeypatch.setattr(service, "get_current", fetch)
    response = await service.get_viewport_current(AsyncMock(), (-47, -24, -43, -22), 0, 20)
    assert [city.id for city in response.cities] == [p[0] for p in points]
    assert fetch.call_count == 1
    assert fetch.call_args.kwargs["include_forecast"] is False
    assert fetch.call_args.args[1] == (("RJ", "Rio de Janeiro", -22.9, -43.2),)
    assert service._cache.get("3304557:current") is not None
    await service.get_viewport_current(AsyncMock(), (-48, -25, -42, -21), 0, 20)
    assert fetch.call_count == 1, "arrastar reaproveita os mesmos municípios"
    service._cache.clear()
