"""Interpolação espacial de dados climáticos (IDW) no backend.

Calcula estimativas suaves de temperatura, umidade e precipitação para municípios
não medidos com base em estações/amostras reais, reduzindo tráfego de rede e
processamento no cliente.
"""

from __future__ import annotations

import math

from app.schemas.weather import WeatherCity


def _geo_dist_sq(lat1: float, lon1: float, lat2: float, lon2: float, cos_lat: float) -> float:
    """Distância ao quadrado considerando curvatura da Terra aproximada local."""
    d_lat = lat1 - lat2
    d_lon = (lon1 - lon2) * cos_lat
    return d_lat * d_lat + d_lon * d_lon


def interpolate_municipal_weather(
    target_code: str,
    target_name: str,
    target_lat: float,
    target_lon: float,
    measured_cities: list[WeatherCity],
    state_abbr: str,
) -> WeatherCity:
    """Estima as condições de um município com base nos k vizinhos mais próximos via IDW."""
    if not measured_cities:
        raise ValueError("Nenhuma cidade medida disponível para interpolação.")

    cos_lat = math.cos(math.radians(target_lat))
    scored: list[tuple[float, WeatherCity]] = []
    for city in measured_cities:
        d_sq = _geo_dist_sq(target_lat, target_lon, city.latitude, city.longitude, cos_lat)
        scored.append((d_sq, city))

    scored.sort(key=lambda item: item[0])
    closest_dist, closest_city = scored[0]

    if closest_dist < 1e-6:
        return closest_city.model_copy(
            update={
                "id": target_code,
                "name": target_name,
                "state_abbreviation": state_abbr or closest_city.state_abbreviation,
                "latitude": target_lat,
                "longitude": target_lon,
                "is_inferred": True,
            }
        )

    k_nearest = scored[: min(3, len(scored))]
    total_weight = 0.0
    weighted_temp = 0.0
    weighted_apparent = 0.0
    weighted_humidity = 0.0
    has_humidity = False
    weighted_precip_sum = 0.0
    has_precip_sum = False
    weighted_precip_prob = 0.0
    has_precip_prob = False
    weighted_rain_24h = 0.0
    has_rain_24h = False

    for dist_sq, city in k_nearest:
        weight = 1.0 / dist_sq
        total_weight += weight
        weighted_temp += city.temperature_c * weight
        apparent = (
            city.apparent_temperature_c
            if city.apparent_temperature_c is not None
            else city.temperature_c
        )
        weighted_apparent += apparent * weight
        if city.humidity_pct is not None:
            weighted_humidity += city.humidity_pct * weight
            has_humidity = True
        precip = (
            city.precipitation_sum_mm
            if city.precipitation_sum_mm is not None
            else city.precipitation_mm
        )
        if precip is not None:
            weighted_precip_sum += precip * weight
            has_precip_sum = True
        if city.precipitation_probability_pct is not None:
            weighted_precip_prob += city.precipitation_probability_pct * weight
            has_precip_prob = True
        if city.precipitation_24h_mm is not None:
            weighted_rain_24h += city.precipitation_24h_mm * weight
            has_rain_24h = True

    est_temp = weighted_temp / total_weight if total_weight > 0 else closest_city.temperature_c
    est_apparent = (
        weighted_apparent / total_weight
        if total_weight > 0
        else closest_city.apparent_temperature_c
    )
    est_humidity = (
        round(weighted_humidity / total_weight) if (has_humidity and total_weight > 0) else None
    )
    est_precip_sum = (
        round((weighted_precip_sum / total_weight), 1)
        if (has_precip_sum and total_weight > 0)
        else 0.0
    )
    est_precip_prob = (
        round(weighted_precip_prob / total_weight)
        if (has_precip_prob and total_weight > 0)
        else None
    )

    return WeatherCity(
        id=target_code,
        name=target_name,
        state_abbreviation=state_abbr or closest_city.state_abbreviation,
        latitude=target_lat,
        longitude=target_lon,
        # Fuso e horário vêm da medição mais próxima: a estimativa não é mais
        # recente que ela, e vários estados não estão no fuso de Brasília.
        timezone=closest_city.timezone,
        observed_at=closest_city.observed_at,
        temperature_c=round(est_temp, 1),
        apparent_temperature_c=round(est_apparent, 1) if est_apparent is not None else None,
        humidity_pct=est_humidity,
        wind_speed_kmh=closest_city.wind_speed_kmh,
        precipitation_mm=closest_city.precipitation_mm or 0.0,
        precipitation_sum_mm=est_precip_sum,
        precipitation_probability_pct=est_precip_prob,
        precipitation_interval_minutes=closest_city.precipitation_interval_minutes,
        precipitation_24h_mm=(
            round(weighted_rain_24h / total_weight, 1) if has_rain_24h and total_weight else None
        ),
        # Chuva agora é local: a estimativa segue a medição mais próxima, não a média.
        raining_now=closest_city.raining_now,
        weather_code=closest_city.weather_code or 0,
        forecast=[],
        is_inferred=True,
    )
