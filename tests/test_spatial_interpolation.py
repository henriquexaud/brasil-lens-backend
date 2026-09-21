"""Testes da interpolação espacial de dados climáticos no backend."""

from datetime import UTC, datetime

import pytest

from app.schemas.weather import WeatherCity
from app.services.spatial_interpolation import interpolate_municipal_weather


def make_city(
    city_id: str,
    name: str,
    lat: float,
    lon: float,
    temp: float,
    weather_code: int = 1,
    rain: float = 0.0,
) -> WeatherCity:
    return WeatherCity(
        id=city_id,
        name=name,
        state_abbreviation="SP",
        latitude=lat,
        longitude=lon,
        timezone="America/Sao_Paulo",
        observed_at=datetime.now(UTC),
        temperature_c=temp,
        apparent_temperature_c=temp,
        humidity_pct=60.0,
        wind_speed_kmh=10.0,
        precipitation_mm=rain,
        precipitation_sum_mm=rain,
        precipitation_probability_pct=20.0,
        precipitation_interval_minutes=15,
        weather_code=weather_code,
        forecast=[],
        is_inferred=False,
    )


def test_interpolate_midpoint_averages_temperatures():
    city_a = make_city("A", "Cidade A", -23.0, -46.0, 20.0, weather_code=1)
    city_b = make_city("B", "Cidade B", -23.0, -47.0, 30.0, weather_code=2)

    # Ponto médio exato entre Cidade A e B
    result = interpolate_municipal_weather(
        target_code="MID",
        target_name="Cidade Meio",
        target_lat=-23.0,
        target_lon=-46.5,
        measured_cities=[city_a, city_b],
        state_abbr="SP",
    )

    assert result.is_inferred is True
    assert result.temperature_c == 25.0
    assert result.name == "Cidade Meio"
    assert result.state_abbreviation == "SP"


def test_interpolate_near_city_picks_closest_attributes():
    city_a = make_city("A", "Cidade A", -23.0, -46.0, 20.0, weather_code=1)
    city_b = make_city("B", "Cidade B", -23.0, -47.0, 30.0, weather_code=2)

    # Ponto quase idêntico a Cidade A
    result = interpolate_municipal_weather(
        target_code="NEAR_A",
        target_name="Cidade Próxima A",
        target_lat=-23.0,
        target_lon=-46.01,
        measured_cities=[city_a, city_b],
        state_abbr="SP",
    )

    assert result.is_inferred is True
    assert result.temperature_c < 21.0
    assert result.weather_code == 1


def test_interpolate_without_cities_raises():
    with pytest.raises(ValueError, match="Nenhuma cidade medida"):
        interpolate_municipal_weather(
            target_code="X",
            target_name="X",
            target_lat=-23.0,
            target_lon=-46.0,
            measured_cities=[],
            state_abbr="SP",
        )

