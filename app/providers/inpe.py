"""Cliente WFS do BDQueimadas: prévia GeoJSON e páginas CSV completas."""

import asyncio
import csv
import io
import math
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import settings
from app.core.cooldown import SourceCooldown
from app.core.errors import ProviderError
from app.schemas.fire_hotspots import (
    FireHotspotFeature,
    FireHotspotProperties,
    FirePoint,
)

inpe_cooldown = SourceCooldown("inpe", 60)
_FIELDS = (
    "id_foco_bdq,data_hora_gmt,satelite,municipio,estado,id_1,id_2,bioma,"
    "precipitacao,numero_dias_sem_chuva,risco_fogo,frp,geometria"
)


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
        # INPE usa valores negativos para informação ausente em campos meteorológicos.
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def parse_feature(raw: dict[str, Any]) -> FireHotspotFeature:
    props = raw["properties"]
    identifier = f"inpe:{int(props['id_foco_bdq'])}"
    detected = datetime.fromisoformat(props["data_hora_gmt"].replace("Z", "+00:00"))
    if detected.tzinfo is None:
        detected = detected.replace(tzinfo=UTC)  # O campo INPE é explicitamente GMT.
    days = _number(props.get("numero_dias_sem_chuva"))
    municipality = props.get("id_2")
    return FireHotspotFeature(
        id=identifier,
        geometry=FirePoint.model_validate(raw["geometry"]),
        properties=FireHotspotProperties(
            id=identifier,
            detected_at=detected.astimezone(UTC),
            satellite=props["satelite"],
            state=props.get("estado"),
            municipality=props.get("municipio"),
            municipality_code=str(municipality) if municipality is not None else None,
            biome=props.get("bioma"),
            days_without_rain=int(days) if days is not None else None,
            precipitation_mm=_number(props.get("precipitacao")),
            fire_risk=_number(props.get("risco_fogo")),
            frp=_number(props.get("frp")),
        ),
    )


async def fetch_features(cql_filter: str, count: int) -> tuple[list[FireHotspotFeature], int]:
    if inpe_cooldown.active:
        raise ProviderError(
            "O INPE não está respondendo. Nova tentativa automática em "
            f"{inpe_cooldown.remaining_seconds()} s."
        )
    try:
        async with httpx.AsyncClient(timeout=settings.inpe_queimadas_http_timeout) as client:
            response = await client.get(
                settings.inpe_queimadas_wfs_url,
                params={
                    "service": "WFS",
                    "version": "2.0.0",
                    "request": "GetFeature",
                    "typeNames": "bdqueimadas:focos",
                    "outputFormat": "application/json",
                    "srsName": "EPSG:4326",
                    "propertyName": _FIELDS,
                    "cql_filter": cql_filter,
                    "count": count,
                    "sortBy": "data_hora_gmt D,id_foco_bdq D",
                },
            )
            response.raise_for_status()
            data = response.json()
        if data["type"] != "FeatureCollection" or not isinstance(data["features"], list):
            raise ValueError("Resposta GeoJSON inválida")
        total = int(data["numberMatched"])
        features = [parse_feature(feature) for feature in data["features"]]
        if total < len(features) or (total > 0 and not features):
            raise ValueError("Contagem de focos inconsistente")
        return features, total
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
        inpe_cooldown.trip()
        raise ProviderError("Não foi possível consultar os focos de calor no INPE.") from exc


async def fetch_rows(
    cql: str, total: int, *, page_size: int, page_concurrency: int
) -> list[dict[str, str]]:
    limit = asyncio.Semaphore(page_concurrency)
    async with httpx.AsyncClient(timeout=settings.inpe_queimadas_http_timeout) as client:

        async def page(start: int) -> list[dict[str, str]]:
            async with limit:
                response = await client.get(
                    settings.inpe_queimadas_wfs_url,
                    params={
                        "service": "WFS",
                        "version": "2.0.0",
                        "request": "GetFeature",
                        "typeNames": "bdqueimadas:focos",
                        "outputFormat": "csv",
                        "propertyName": "id_foco_bdq,id_1,id_2,data_hora_gmt",
                        "cql_filter": cql,
                        "count": min(page_size, total - start),
                        "startIndex": start,
                        "sortBy": "data_hora_gmt D,id_foco_bdq D",
                    },
                )
            response.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(response.text)))
            if not rows or "id_foco_bdq" not in rows[0]:
                raise ValueError("Página de focos incompleta")
            return rows

        tasks = [asyncio.create_task(page(start)) for start in range(0, total, page_size)]
        try:
            pages = await asyncio.gather(*tasks)
        finally:
            # Uma página falhou: as que ainda esperam a vez não vão à fonte.
            for task in tasks:
                task.cancel()
    rows = [row for page_rows in pages for row in page_rows]
    # Páginas truncadas ou deslocadas (a fonte mudou no meio) não passam.
    if len(rows) != total or len({row["id_foco_bdq"] for row in rows}) != total:
        raise ValueError("Contagem de focos mudou durante a consulta; tente novamente")
    return rows
