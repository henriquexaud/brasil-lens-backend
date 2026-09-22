"""Regras de apresentação da camada meteorológica.

Curto de propósito: ao contrário de `services/map.py`, não há `latest` para
resolver nem estatística — uma estação mostra sua **última** leitura (já
resolvida em `repositories/weather.py` via `LATERAL`), e um alerta é ativo ou
não aparece. A única classificação daqui é a de alertas: `category` e
`severity_level` traduzem o vocabulário de cada fonte (INMET, CEMADEN) para
um esquema comum que o frontend consome sem precisar conhecer nenhuma das
duas (`_category_for`/`_severity_level_for`).
"""

from __future__ import annotations

from datetime import UTC, datetime

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.core.config import settings
from app.repositories import weather as weather_repo
from app.repositories.weather import SourceStatusRow
from app.schemas.weather import (
    WeatherAlertCategory,
    WeatherAlertCollection,
    WeatherAlertFeature,
    WeatherAlertProperties,
    WeatherAlertSeverityLevel,
    WeatherSourcesResponse,
    WeatherSourceStatus,
    WeatherSourceStatusValue,
    WeatherStationCollection,
    WeatherStationFeature,
    WeatherStationProperties,
)

# Apenas avisos/alertas oficiais são ingeridos automaticamente. Condições e
# previsão são consultadas em services/weather_forecast.py, com cache
# independente.
_SOURCE_DEFINITIONS: tuple[tuple[str, str, str, str | None], ...] = (
    ("import_weather_inmet_alerts", "inmet_alerts", "INMET — Avisos Meteorológicos", None),
    (
        "import_weather_cemaden_alerts",
        "cemaden_alerts",
        "CEMADEN — Alertas de Risco Geo-Hidrológico",
        None,
    ),
)

# Categoria comum: hoje é 100% função da fonte (o INMET só emite fenômeno
# meteorológico; o CEMADEN só emite risco geo-hidrológico) — sem ambiguidade
# real que justifique guardar isto como coluna (ver app/models/weather.py).
_CATEGORY_BY_PROVIDER: dict[str, WeatherAlertCategory] = {
    "cemaden": WeatherAlertCategory.GEO_HYDROLOGICAL,
}


def _category_for(provider: str) -> WeatherAlertCategory:
    return _CATEGORY_BY_PROVIDER.get(provider, WeatherAlertCategory.METEOROLOGICAL)


def _severity_level_for(provider: str, severity: str) -> WeatherAlertSeverityLevel:
    """Classificação comum de severidade — a mesma escala de 3 níveis que o
    frontend já desenhava antes (`AlertSeverityTier`), calculada aqui uma vez
    em vez de adivinhada em cada render a partir de texto/cor por fonte (ver
    `alertStyles.ts`). O vocabulário de `severity` é por fonte — INMET fala em
    "perigo", CEMADEN em "alto"/"moderado" — por isso `provider` entra na
    decisão, não só o texto.
    """
    text = (severity or "").strip().lower()
    if provider == "cemaden":
        if text == "muito alto":
            return WeatherAlertSeverityLevel.EXTREME
        if text == "alto":
            return WeatherAlertSeverityLevel.DANGER
        if text == "moderado":
            return WeatherAlertSeverityLevel.POTENTIAL
        return WeatherAlertSeverityLevel.OTHER
    # INMET: "potencial" checado antes de "perigo" — "Perigo Potencial" contém
    # as duas palavras (mesma ordem de checagem que alertStyles.ts já usava).
    if "grande perigo" in text:
        return WeatherAlertSeverityLevel.EXTREME
    if "potencial" in text:
        return WeatherAlertSeverityLevel.POTENTIAL
    if "perigo" in text:
        return WeatherAlertSeverityLevel.DANGER
    return WeatherAlertSeverityLevel.OTHER


# Uma fonte sem execução bem-sucedida há mais que isto (múltiplo da cadência
# esperada) deixa de ser "ok" — tolera um ciclo perdido sem virar alarme falso.
_STALE_MULTIPLIER = 3

# TTL curto de propósito: o dado já é barato de ler (Postgres, não a fonte
# externa — ver docstring do módulo). Isto só evita reler a cada poll do
# frontend (`refetchInterval`) durante picos de tráfego, não substitui o
# scheduler como mecanismo de frescor.
_STATIONS_CACHE_KEY = "stations"
_ALERTS_CACHE_KEY = "alerts"
_stations_cache: TTLCache[WeatherStationCollection] = TTLCache(
    ttl_seconds=settings.weather_stations_cache_ttl_seconds, max_entries=1
)
_alerts_cache: TTLCache[WeatherAlertCollection] = TTLCache(
    ttl_seconds=settings.weather_alerts_cache_ttl_seconds, max_entries=1
)


async def get_stations(session: AsyncSession) -> WeatherStationCollection:
    cached = _stations_cache.get(_STATIONS_CACHE_KEY)
    if cached is not None:
        return cached

    rows = await weather_repo.list_current_stations(session)
    collection = WeatherStationCollection(
        features=[
            WeatherStationFeature(
                id=f"{row.provider}:{row.external_code}",
                properties=WeatherStationProperties(
                    provider=row.provider,
                    external_code=row.external_code,
                    name=row.name,
                    station_type=row.station_type,
                    state_abbreviation=row.state_abbreviation,
                    observed_at=row.observed_at,
                    temperature_c=row.temperature_c,
                    humidity_pct=row.humidity_pct,
                    pressure_hpa=row.pressure_hpa,
                    precipitation_mm=row.precipitation_mm,
                ),
                geometry=orjson.loads(row.geometry_json),
            )
            for row in rows
        ]
    )
    _stations_cache.set(_STATIONS_CACHE_KEY, collection)
    return collection


async def get_alerts(session: AsyncSession) -> WeatherAlertCollection:
    cached = _alerts_cache.get(_ALERTS_CACHE_KEY)
    if cached is not None:
        return cached

    rows = await weather_repo.list_active_alerts(session)
    collection = WeatherAlertCollection(
        features=[
            WeatherAlertFeature(
                id=f"{row.provider}:{row.external_id}",
                properties=WeatherAlertProperties(
                    provider=row.provider,
                    category=_category_for(row.provider),
                    event=row.event,
                    severity=row.severity,
                    severity_level=_severity_level_for(row.provider, row.severity),
                    color=row.color,
                    description=row.description,
                    onset=row.onset,
                    expires=row.expires,
                    affected_ibge_codes=row.affected_ibge_codes,
                    risks=row.risks,
                    instructions=row.instructions,
                ),
                geometry=orjson.loads(row.geometry_json),
            )
            for row in rows
        ]
    )
    _alerts_cache.set(_ALERTS_CACHE_KEY, collection)
    return collection


async def get_sources(session: AsyncSession) -> WeatherSourcesResponse:
    job_names = [job for job, _, _, _ in _SOURCE_DEFINITIONS]
    runs = await weather_repo.latest_run_per_job(session, job_names)

    frequency = settings.weather_refresh_interval_seconds
    sources = []
    for job, key, name, zero_output_key in _SOURCE_DEFINITIONS:
        status, last_updated_at = _status_for(
            runs.get(job), frequency_seconds=frequency, zero_output_key=zero_output_key
        )
        sources.append(
            WeatherSourceStatus(
                key=key,
                name=name,
                last_updated_at=last_updated_at,
                status=status,
                update_frequency_seconds=frequency,
            )
        )
    return WeatherSourcesResponse(sources=sources)


def _status_for(
    row: SourceStatusRow | None,
    *,
    frequency_seconds: int,
    zero_output_key: str | None,
) -> tuple[WeatherSourceStatusValue, datetime | None]:
    """`unavailable` cobre "nunca rodou", "rodou e falhou" e "rodou, disse que
    teve sucesso, mas não produziu o que deveria produzir" com o mesmo
    rótulo: das três perspectivas do frontend, não há dado confiável para
    mostrar. `stale` existe separado de `unavailable` porque uma fonte que só
    está atrasada (ciclo perdido) é uma situação diferente de uma fonte sem
    produção real (ver `app/providers/inmet/stations.py` e o comentário em
    `_SOURCE_DEFINITIONS`).
    """
    if row is None or row.finished_at is None or row.status == "failed":
        return WeatherSourceStatusValue.UNAVAILABLE, None

    if zero_output_key is not None and not (row.details or {}).get(zero_output_key):
        return WeatherSourceStatusValue.UNAVAILABLE, None

    elapsed_seconds = (datetime.now(UTC) - row.finished_at).total_seconds()
    if elapsed_seconds > frequency_seconds * _STALE_MULTIPLIER:
        return WeatherSourceStatusValue.STALE, row.finished_at
    return WeatherSourceStatusValue.OK, row.finished_at
