from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import orjson

from app.core.errors import ProviderError
from app.providers.base import get_json
from app.providers.records import WeatherAlertRecord

SOURCE = "inmet_avisos"
PROVIDER_KEY = "inmet"


async def fetch_active_alerts(client: httpx.AsyncClient) -> list[WeatherAlertRecord]:
    raw = await get_json(client, "/avisos/ativos", source=SOURCE)
    if not isinstance(raw, dict) or not isinstance(raw.get("hoje"), list):
        raise ProviderError(f"{SOURCE} devolveu formato inesperado para /avisos/ativos.")

    records: list[WeatherAlertRecord] = []
    for entry in raw["hoje"]:
        if not isinstance(entry, dict) or entry.get("encerrado"):
            continue
        record = _to_alert(entry)
        if record is not None:
            records.append(record)
    return records


def _to_alert(entry: dict[str, Any]) -> WeatherAlertRecord | None:
    external_id = entry.get("id_aviso")
    onset = _parse_iso(entry.get("data_inicio"))
    expires = _parse_iso(entry.get("data_fim"))
    polygon = _parse_polygon(entry.get("poligono"))
    if external_id is None or onset is None or expires is None or polygon is None:
        return None

    return WeatherAlertRecord(
        provider=PROVIDER_KEY,
        external_id=str(external_id),
        event=entry.get("descricao") or "Aviso meteorológico",
        severity=entry.get("severidade") or "Desconhecida",
        onset=onset,
        expires=expires,
        polygon_geojson=polygon,
        color=entry.get("aviso_cor"),
        affected_ibge_codes=_split_codes(entry.get("geocodes")),
        risks=tuple(entry.get("riscos") or ()),
        instructions=tuple(entry.get("instrucoes") or ()),
    )


def _parse_polygon(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = orjson.loads(value)
    except orjson.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _split_codes(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, str) or not raw.strip():
        return ()
    return tuple(code.strip() for code in raw.split(",") if code.strip().isdigit())
