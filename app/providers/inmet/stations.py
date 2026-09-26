"""Estações meteorológicas automáticas do INMET.

Duas chamadas, contratos verificados de formas diferentes:

1. **`GET /estacoes/T`** — metadado de todas as estações automáticas.
   **Confirmado por chamada HTTP real** durante o desenho deste provider:
   devolve uma lista de objetos com `CD_ESTACAO`, `DC_NOME`, `SG_ESTADO`,
   `VL_LATITUDE`, `VL_LONGITUDE`, `CD_SITUACAO` ("Operante"/"Pane"), entre
   outros.
2. **`GET /estacao/{inicio}/{fim}/{codigo}`** — série horária de UMA estação
   num intervalo de datas. Os nomes de campo (`CD_ESTACAO`, `DT_MEDICAO`,
   `HR_MEDICAO`, `TEM_INS`, `UMD_INS`, `PRE_INS`, `CHUVA`) vêm confirmados
   contra o código-fonte do cliente Python `inmetpy`. **O endpoint em si foi
   confirmado rodando o job de verdade** contra a fonte: a primeira execução
   real (518 estações) devolveu, para TODAS elas, HTTP 200 com corpo
   `"Você atingiu o limite de requisições."` em texto plano — a fonte tem
   *rate limit* agressivo (a chave certa é `/estacao/{inicio}/{fim}/{codigo}`,
   não outra coisa: só a concorrência é que estava errada). `_get_with_retry`
   abaixo trata esse texto como sinal de "tente de novo depois", do mesmo
   jeito que `providers/base.py` já trata HTTP 429 — só que aqui o sinal vem
   no corpo, não no status, porque é assim que esta fonte específica sinaliza.

Iteração estação-por-estação (não um endpoint "todas as estações hoje") pelo
mesmo motivo que a importação das malhas consulta municípios UF-por-UF: um
payload de centenas de estações × dias de uma vez é a forma mais confiável de
tomar timeout — e o candidato a endpoint em lote (`/estacao/dados/{data}`,
documentado por clientes de terceiros) devolveu 404 confirmado por chamada
real, então não é usado aqui.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.core.errors import ProviderError
from app.models import WeatherStationType
from app.providers.base import get_json
from app.providers.records import WeatherObservationRecord, WeatherStationRecord

SOURCE = "inmet_estacoes"
PROVIDER_KEY = "inmet"

_OPERATING_STATUS = "Operante"
# Tolera atraso de publicação da fonte sem pedir um histórico grande a cada
# ciclo do scheduler.
_LOOKBACK_DAYS = 2
# Sentinela de ausência do INMET nos campos numéricos (ex.: -9999).
_MISSING_THRESHOLD = Decimal("-9990")

# Texto literal com que a fonte sinaliza limite de requisições — confirmado
# por chamada real (ver docstring do módulo). HTTP 200, não 429: o sinal vem
# no corpo, então `get_json` (que só reconhece status) não retenta sozinho.
_RATE_LIMIT_MARKER = "limite de requisições"
_RATE_LIMIT_MAX_ATTEMPTS = 5
_RATE_LIMIT_BACKOFF_SECONDS = 4.0


async def _get_json_with_rate_limit_retry(client: httpx.AsyncClient, path: str) -> Any:
    for attempt in range(1, _RATE_LIMIT_MAX_ATTEMPTS + 1):
        try:
            return await get_json(client, path, source=SOURCE)
        except ProviderError as exc:
            body = str(exc.details.get("body") or "")
            if _RATE_LIMIT_MARKER not in body or attempt == _RATE_LIMIT_MAX_ATTEMPTS:
                raise
            await asyncio.sleep(_RATE_LIMIT_BACKOFF_SECONDS * attempt)
    raise AssertionError("inalcançável: o laço sempre retorna ou levanta")  # pragma: no cover


async def fetch_stations(client: httpx.AsyncClient) -> list[WeatherStationRecord]:
    """Metadado de todas as estações automáticas em operação."""
    raw = await get_json(client, "/estacoes/T", source=SOURCE)
    if not isinstance(raw, list):
        raise ProviderError(f"{SOURCE} devolveu formato inesperado para /estacoes/T.")

    records: list[WeatherStationRecord] = []
    for entry in raw:
        if not isinstance(entry, dict) or entry.get("CD_SITUACAO") != _OPERATING_STATUS:
            continue
        code = entry.get("CD_ESTACAO")
        latitude = _to_float(entry.get("VL_LATITUDE"))
        longitude = _to_float(entry.get("VL_LONGITUDE"))
        if not code or latitude is None or longitude is None:
            continue
        records.append(
            WeatherStationRecord(
                provider=PROVIDER_KEY,
                external_code=code,
                name=entry.get("DC_NOME") or code,
                station_type=WeatherStationType.AUTOMATIC_WEATHER,
                latitude=latitude,
                longitude=longitude,
                state_abbreviation=entry.get("SG_ESTADO"),
            )
        )
    return records


async def fetch_latest_observation(
    client: httpx.AsyncClient, station_code: str
) -> WeatherObservationRecord | None:
    """A leitura mais recente de uma estação, dentro da janela de tolerância.

    `None` quando a fonte não publicou nenhuma leitura na janela — não é erro
    (estações caem, ver `CD_SITUACAO`), é "sem dado agora". **Confirmado por
    chamada real**: a fonte devolve HTTP 204 (sem corpo) nesse caso — não um
    array vazio — então isso precisa ser tratado aqui, e não em `get_json`
    (genérico demais para saber que 204 é normal só *nesta* rota).
    """
    end = datetime.now(UTC).date()
    start = end - timedelta(days=_LOOKBACK_DAYS)
    path = f"/estacao/{start.isoformat()}/{end.isoformat()}/{station_code}"
    try:
        raw = await _get_json_with_rate_limit_retry(client, path)
    except ProviderError as exc:
        if "HTTP 204" in str(exc):
            return None
        raise
    if not isinstance(raw, list) or not raw:
        return None

    observations = [
        obs
        for entry in raw
        if isinstance(entry, dict) and (obs := _to_observation(station_code, entry)) is not None
    ]
    if not observations:
        return None
    return max(observations, key=lambda obs: obs.observed_at)


def _to_observation(station_code: str, entry: dict[str, Any]) -> WeatherObservationRecord | None:
    observed_at = _parse_measured_at(entry.get("DT_MEDICAO"), entry.get("HR_MEDICAO"))
    if observed_at is None:
        return None
    return WeatherObservationRecord(
        provider=PROVIDER_KEY,
        external_code=station_code,
        observed_at=observed_at,
        temperature_c=_to_decimal(entry.get("TEM_INS")),
        humidity_pct=_to_decimal(entry.get("UMD_INS")),
        pressure_hpa=_to_decimal(entry.get("PRE_INS")),
        precipitation_mm=_to_decimal(entry.get("CHUVA")),
    )


def _parse_measured_at(raw_date: Any, raw_time: Any) -> datetime | None:
    """`DT_MEDICAO` ("2026-09-19") + `HR_MEDICAO` ("0000", UTC) → datetime tz-aware."""
    if not raw_date or raw_time is None:
        return None
    try:
        day = date.fromisoformat(str(raw_date)[:10])
        padded = str(raw_time).strip().zfill(4)
        hour, minute = int(padded[:2]), int(padded[2:4])
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if parsed <= _MISSING_THRESHOLD:
        return None
    return parsed


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
