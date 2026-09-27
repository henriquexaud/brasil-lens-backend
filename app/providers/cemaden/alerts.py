from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import ProviderError
from app.providers.base import get_json
from app.providers.records import WeatherAlertRecord

SOURCE = "cemaden_alertas"
PROVIDER_KEY = "cemaden"
TYPE_NAME = "cemaden_dev:alertas_vigentes_siaden"

_LEVEL_COLORS: dict[str, str] = {
    "Moderado": "#FFFF00",
    "Alto": "#FFA500",
    "Muito Alto": "#FF0000",
}

_PROPERTIES = "id_alerta,datahoracriacao,cidade,uf,evento,nivel,status,path_pdf,codibge,the_geom"


async def fetch_active_alerts(client: httpx.AsyncClient) -> list[WeatherAlertRecord]:
    raw = await get_json(
        client,
        "/ows",
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeName": TYPE_NAME,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "propertyName": _PROPERTIES,
            "cql_filter": "status=1",
        },
        source=SOURCE,
    )
    if not isinstance(raw, dict) or not isinstance(raw.get("features"), list):
        raise ProviderError(f"{SOURCE} devolveu formato inesperado para GetFeature.")

    fetched_at = datetime.now(UTC)
    records: list[WeatherAlertRecord] = []
    for feature in raw["features"]:
        if not isinstance(feature, dict):
            continue
        record = _to_alert(feature, fetched_at)
        if record is not None:
            records.append(record)
    return records


def _to_alert(feature: dict[str, Any], fetched_at: datetime) -> WeatherAlertRecord | None:
    props = feature.get("properties")
    geometry = feature.get("geometry")
    if not isinstance(props, dict) or not isinstance(geometry, dict):
        return None
    if props.get("status") != 1:
        return None

    external_id = props.get("id_alerta")
    onset = _parse_iso(props.get("datahoracriacao"))
    nivel = props.get("nivel")
    event = _event_type(props.get("evento"), nivel)
    if external_id is None or onset is None or event is None:
        return None

    expires = fetched_at + timedelta(seconds=settings.cemaden_alert_validity_buffer_seconds)

    return WeatherAlertRecord(
        provider=PROVIDER_KEY,
        external_id=str(external_id),
        event=event,
        severity=nivel if isinstance(nivel, str) and nivel else "Desconhecida",
        onset=onset,
        expires=expires,
        polygon_geojson=geometry,
        color=_LEVEL_COLORS.get(nivel) if isinstance(nivel, str) else None,
        description=_description(props.get("cidade"), props.get("uf")),
        affected_ibge_codes=_ibge_code(props.get("codibge")),
        instructions=_pdf_instruction(props.get("path_pdf")),
    )


def _event_type(evento: Any, nivel: Any) -> str | None:
    if not isinstance(evento, str) or not evento.strip():
        return None
    cleaned = evento.strip()
    if isinstance(nivel, str) and nivel:
        suffix = f" - {nivel}"
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return cleaned.strip() or None


def _description(cidade: Any, uf: Any) -> str | None:
    if not isinstance(cidade, str) or not cidade.strip():
        return None
    city = cidade.strip()
    return f"{city}/{uf.strip()}" if isinstance(uf, str) and uf.strip() else city


def _ibge_code(value: Any) -> tuple[str, ...]:
    if isinstance(value, bool):
        return ()
    if isinstance(value, int):
        return (str(value),)
    if isinstance(value, str) and value.strip().isdigit():
        return (value.strip(),)
    return ()


def _pdf_instruction(path_pdf: Any) -> tuple[str, ...]:
    if not isinstance(path_pdf, str) or not path_pdf.strip():
        return ()
    return (f"Boletim oficial do CEMADEN: {path_pdf.strip()}",)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
