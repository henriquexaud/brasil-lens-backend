from __future__ import annotations

from typing import Any, Literal

import httpx

from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.providers.base import get_json
from app.providers.records import GeometryRecord

logger = get_logger(__name__)

SOURCE = "ibge"
DATASET_CODE = "malhas/v3"
DATASET_NAME = "IBGE — Malhas Territoriais (API de Malhas v3, qualidade máxima)"
DATASET_URL = "https://servicodados.ibge.gov.br/api/docs/malhas"

Quality = Literal["minima", "intermediaria", "maxima"]

_GEOJSON_FORMAT = "application/vnd.geo+json"


async def fetch_country(client: httpx.AsyncClient, quality: Quality = "maxima") -> GeometryRecord:
    features = await _fetch_features(client, "/api/v3/malhas/paises/BR", quality=quality)
    return _single(features, scope="paises/BR")


async def fetch_regions(
    client: httpx.AsyncClient, quality: Quality = "maxima"
) -> list[GeometryRecord]:
    return await _fetch_features(
        client, "/api/v3/malhas/paises/BR", quality=quality, intrarregiao="regiao"
    )


async def fetch_states(
    client: httpx.AsyncClient, quality: Quality = "maxima"
) -> list[GeometryRecord]:
    return await _fetch_features(
        client, "/api/v3/malhas/paises/BR", quality=quality, intrarregiao="UF"
    )


async def fetch_municipalities_of_state(
    client: httpx.AsyncClient,
    state_ibge_code: str,
    quality: Quality = "maxima",
) -> list[GeometryRecord]:
    return await _fetch_features(
        client,
        f"/api/v3/malhas/estados/{state_ibge_code}",
        quality=quality,
        intrarregiao="municipio",
    )


async def _fetch_features(
    client: httpx.AsyncClient,
    path: str,
    *,
    quality: Quality,
    intrarregiao: str | None = None,
) -> list[GeometryRecord]:
    params: dict[str, Any] = {"formato": _GEOJSON_FORMAT, "qualidade": quality}
    if intrarregiao is not None:
        params["intrarregiao"] = intrarregiao

    payload = await get_json(client, path, params=params, source="IBGE Malhas")

    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise ProviderError(
            "IBGE Malhas não devolveu um FeatureCollection.",
            path=path,
            received=str(payload)[:300],
        )

    records: list[GeometryRecord] = []
    for feature in payload.get("features", []):
        code = (feature.get("properties") or {}).get("codarea")
        geometry = feature.get("geometry")
        if not code or not geometry:
            raise ProviderError(
                "Feature sem 'codarea' ou sem geometria na malha do IBGE.",
                path=path,
            )
        records.append(GeometryRecord(ibge_code=str(code), geojson=geometry))

    logger.info(
        "malhas.fetched",
        extra={"path": path, "intrarregiao": intrarregiao, "features": len(records)},
    )
    return records


def _single(records: list[GeometryRecord], *, scope: str) -> GeometryRecord:
    if len(records) != 1:
        raise ProviderError(
            f"Esperava exatamente 1 feature em {scope}, recebi {len(records)}.",
            scope=scope,
        )
    return records[0]
