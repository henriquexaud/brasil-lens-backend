"""Área visível: uma leitura por célula, conforme o zoom; os vizinhos são estimados."""

from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.providers import open_meteo
from app.repositories import territories, viewport
from app.services import weather_forecast as service

# Quatro municípios numa célula de 0,5° e dois em outra.
POINTS = [
    ("3500105", "Amostra", -23.10, -46.90),
    ("3500204", "Centro A", -23.26, -46.74),
    ("3500303", "Borda A", -23.45, -46.55),
    ("3500402", "Outra A", -23.02, -46.98),
    ("3500501", "Centro B", -23.24, -46.26),
    ("3500600", "Borda B", -23.05, -46.05),
]


@pytest.fixture(autouse=True)
def synthetic_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[AsyncMock]:
    service.reset_state()
    monkeypatch.setattr(service, "STATE_SAMPLE_SIZE", 1)
    monkeypatch.setattr(territories, "list_weather_points", AsyncMock(return_value=POINTS))
    visible = AsyncMock(return_value=[(c, n, "SP", lat, lon) for c, n, lat, lon in POINTS])
    monkeypatch.setattr(viewport, "weather_points", visible)
    yield visible
    service.reset_state()


def fetch_mock() -> AsyncMock:
    async def fetch(client, locations, *, include_forecast):
        raw = {
            "timezone": "America/Sao_Paulo",
            "current": {
                "time": datetime.now(UTC).timestamp(),
                "interval": 900,
                "temperature_2m": 25.0,
                "precipitation": 0,
                "weather_code": 0,
            },
        }
        return [open_meteo._parse_city(raw, location) for location in locations]

    return AsyncMock(side_effect=fetch)


async def test_zoom_8_measures_one_municipality_per_cell(monkeypatch, synthetic_state) -> None:
    fetch = fetch_mock()
    monkeypatch.setattr(service, "fetch_locations", fetch)
    response = await service.get_viewport_current(AsyncMock(), (-47.5, -24, -45.5, -22.5), 8)
    names = [location[1] for location in fetch.await_args.args[1]]
    # A amostra do estado representa a sua célula; na outra, o mais central.
    assert sorted(names) == ["Amostra", "Centro B"]
    assert [city.id for city in response.cities] == [p[0] for p in POINTS]
    measured = {city.name for city in response.cities if not city.is_inferred}
    assert measured == {"Amostra", "Centro B"}
    assert all(city.temperature_c == 25.0 for city in response.cities)
    assert synthetic_state.call_args.kwargs["parent"] is None


async def test_close_zoom_measures_every_visible_municipality(monkeypatch) -> None:
    fetch = fetch_mock()
    monkeypatch.setattr(service, "fetch_locations", fetch)
    response = await service.get_viewport_current(
        AsyncMock(), (-47.2, -23.6, -45.9, -22.9), 10, parent="35"
    )
    assert len(fetch.await_args.args[1]) == len(POINTS)
    assert not any(city.is_inferred for city in response.cities)


async def test_known_readings_are_reused_across_zoom_levels(monkeypatch) -> None:
    fetch = fetch_mock()
    monkeypatch.setattr(service, "fetch_locations", fetch)
    await service.get_viewport_current(AsyncMock(), (-47.5, -24, -45.5, -22.5), 8)
    await service.get_viewport_current(AsyncMock(), (-47.2, -23.6, -45.9, -22.9), 10)
    fetched = [location[1] for call in fetch.await_args_list for location in call.args[1]]
    assert sorted(fetched) == sorted(name for _, name, _, _ in POINTS)


async def test_dense_area_measures_on_a_coarser_grid(monkeypatch) -> None:
    fetch = fetch_mock()
    monkeypatch.setattr(service, "fetch_locations", fetch)
    monkeypatch.setattr(service, "MAX_MEASURED_PER_VIEW", 1)
    response = await service.get_viewport_current(
        AsyncMock(), (-47.2, -23.6, -45.9, -22.9), 10, parent="35"
    )
    # Seis municípios no zoom 10 seriam todos medidos; com o teto, um por célula grande.
    assert len(fetch.await_args.args[1]) == 1
    assert sum(not city.is_inferred for city in response.cities) == 1
    assert len(response.cities) == len(POINTS)
