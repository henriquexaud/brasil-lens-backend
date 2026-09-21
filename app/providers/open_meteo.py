"""Condições modeladas e previsão das 27 capitais, em uma única chamada pública.

Contrato: https://open-meteo.com/en/docs. Não são medições de estações.
Coordenadas aproximadas dos centros urbanos, independentes da ingestão IBGE.
"""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.core.errors import ProviderError, ProviderRateLimitedError
from app.models import DataContext
from app.providers.descriptor import ProviderDescriptor
from app.schemas.weather import WeatherCity, WeatherForecastDay

PROVIDER = ProviderDescriptor(
    key="open_meteo",
    name="Open-Meteo — Condições atuais e previsão",
    context=DataContext.CLIMATE_ENVIRONMENTAL,
    provides=(),
    homepage="https://open-meteo.com/",
)

# UF, capital, latitude, longitude.
CAPITALS = (
    ("AC", "Rio Branco", -9.9754, -67.8249),
    ("AL", "Maceió", -9.6658, -35.7353),
    ("AP", "Macapá", 0.0349, -51.0694),
    ("AM", "Manaus", -3.1190, -60.0217),
    ("BA", "Salvador", -12.9714, -38.5014),
    ("CE", "Fortaleza", -3.7172, -38.5433),
    ("DF", "Brasília", -15.7939, -47.8828),
    ("ES", "Vitória", -20.3155, -40.3128),
    ("GO", "Goiânia", -16.6869, -49.2648),
    ("MA", "São Luís", -2.5297, -44.2825),
    ("MT", "Cuiabá", -15.6014, -56.0979),
    ("MS", "Campo Grande", -20.4697, -54.6201),
    ("MG", "Belo Horizonte", -19.9167, -43.9345),
    ("PA", "Belém", -1.4558, -48.4902),
    ("PB", "João Pessoa", -7.1195, -34.8450),
    ("PR", "Curitiba", -25.4284, -49.2733),
    ("PE", "Recife", -8.0476, -34.8770),
    ("PI", "Teresina", -5.0892, -42.8019),
    ("RJ", "Rio de Janeiro", -22.9068, -43.1729),
    ("RN", "Natal", -5.7945, -35.2110),
    ("RS", "Porto Alegre", -30.0346, -51.2177),
    ("RO", "Porto Velho", -8.7608, -63.8999),
    ("RR", "Boa Vista", 2.8235, -60.6758),
    ("SC", "Florianópolis", -27.5954, -48.5480),
    ("SP", "São Paulo", -23.5505, -46.6333),
    ("SE", "Aracaju", -10.9472, -37.0731),
    ("TO", "Palmas", -10.1840, -48.3336),
)

# Cota do plano gratuito (600/min, 5.000/h, 10.000/dia por IP). A fonte responde
# 429 com `{"reason": "Daily API request limit exceeded..."}` e não documenta
# quando cada janela renova — por isso a espera é um intervalo de sondagem (a
# próxima consulta real é a sonda), não um horário calculado.
_RATE_LIMIT_WINDOWS = (
    (
        "minutely",
        60,
        "Muitas consultas em pouco tempo à fonte de clima (Open-Meteo). "
        "Tente novamente em instantes.",
    ),
    (
        "hourly",
        300,
        "O limite por hora de consultas da fonte de clima (Open-Meteo) foi atingido. "
        "Tente novamente em alguns minutos.",
    ),
    (
        "daily",
        900,
        "O limite diário de consultas da fonte de clima (Open-Meteo) foi atingido. "
        "Os dados voltam quando a cota for renovada.",
    ),
)
_RATE_LIMIT_UNKNOWN = (
    300,
    "A fonte de clima (Open-Meteo) recusou a consulta por excesso de requisições. "
    "Tente novamente em alguns minutos.",
)


def _rate_limit_error(response: httpx.Response) -> ProviderRateLimitedError:
    try:
        reason = str(response.json().get("reason", "")).lower()
    except (ValueError, AttributeError):
        reason = ""
    wait, message = next(
        ((wait, text) for window, wait, text in _RATE_LIMIT_WINDOWS if window in reason),
        _RATE_LIMIT_UNKNOWN,
    )
    retry_after = response.headers.get("retry-after", "")
    return ProviderRateLimitedError(message, int(retry_after) if retry_after.isdigit() else wait)


async def fetch_locations(
    client: httpx.AsyncClient,
    locations: tuple[tuple[str, str, float, float], ...] = CAPITALS,
    *,
    include_forecast: bool = True,
) -> list[WeatherCity]:
    try:
        response = await client.get(
            "/v1/forecast",
            params={
                "latitude": ",".join(str(city[2]) for city in locations),
                "longitude": ",".join(str(city[3]) for city in locations),
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "precipitation,weather_code,wind_speed_10m",
                **(
                    {
                        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                        "precipitation_probability_max,precipitation_sum"
                    }
                    if include_forecast
                    else {}
                ),
                "timezone": "auto",
                "forecast_days": 3 if include_forecast else 1,
                "timeformat": "unixtime",
                "temperature_unit": "celsius",
                "wind_speed_unit": "kmh",
                "precipitation_unit": "mm",
            },
        )
        if response.status_code == 429:
            raise _rate_limit_error(response)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and len(locations) == 1:
            payload = [payload]
        if not isinstance(payload, list) or len(payload) != len(locations):
            raise ValueError("Resposta incompleta para os locais solicitados")
        return [
            _parse_city(raw, location) for raw, location in zip(payload, locations, strict=True)
        ]
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as exc:
        raise ProviderError("Não foi possível consultar o clima na Open-Meteo.") from exc


def _parse_city(raw: dict[str, Any], capital: tuple[str, str, float, float]) -> WeatherCity:
    state, name, latitude, longitude = capital
    current, daily = raw["current"], raw.get("daily", {})
    # UNIX mantém o instante em UTC; a data diária precisa do fuso da cidade.
    timezone = ZoneInfo(raw["timezone"])
    precip_sums = daily.get("precipitation_sum") or []
    precip_probs = daily.get("precipitation_probability_max") or []
    return WeatherCity(
        id=state,
        name=name,
        state_abbreviation=state,
        latitude=latitude,
        longitude=longitude,
        timezone=raw["timezone"],
        observed_at=datetime.fromtimestamp(current["time"], UTC),
        temperature_c=current["temperature_2m"],
        apparent_temperature_c=current.get("apparent_temperature"),
        humidity_pct=current.get("relative_humidity_2m"),
        wind_speed_kmh=current.get("wind_speed_10m"),
        precipitation_mm=current.get("precipitation"),
        precipitation_sum_mm=precip_sums[0] if precip_sums else current.get("precipitation"),
        precipitation_probability_pct=precip_probs[0] if precip_probs else None,
        precipitation_interval_minutes=current["interval"] // 60,
        weather_code=current.get("weather_code"),
        forecast=[
            WeatherForecastDay(
                date=datetime.fromtimestamp(timestamp, timezone).date(),
                weather_code=daily["weather_code"][i],
                temperature_min_c=daily["temperature_2m_min"][i],
                temperature_max_c=daily["temperature_2m_max"][i],
                precipitation_probability_pct=daily["precipitation_probability_max"][i],
                precipitation_sum_mm=precip_sums[i] if i < len(precip_sums) else None,
            )
            for i, timestamp in enumerate(daily.get("time", []))
            if "weather_code" in daily
        ],
    )
