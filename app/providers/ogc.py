"""Leitura paginada de serviços geográficos oficiais: WFS (GeoServer) e ArcGIS REST.

Só a ingestão usa: nenhuma rota do mapa consulta essas fontes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.errors import ProviderError
from app.providers.base import get_json


def _features(payload: Any, source: str, url: str) -> list[dict[str, Any]]:
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        message = payload.get("error") if isinstance(payload, dict) else None
        raise ProviderError(
            f"{source} respondeu num formato inesperado.", url=url, body=str(message)[:300]
        )
    return features


async def wfs_features(
    client: httpx.AsyncClient,
    url: str,
    type_name: str,
    *,
    source: str,
    sort_by: str,
    page_size: int = 1000,
    cql_filter: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Feições GeoJSON de uma camada WFS, em páginas ordenadas (`sort_by` estabiliza a ordem)."""
    start = 0
    while True:
        params: dict[str, Any] = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": type_name,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "sortBy": sort_by,
            "count": page_size,
            "startIndex": start,
        }
        if cql_filter:
            params["cql_filter"] = cql_filter
        page = _features(await get_json(client, url, params=params, source=source), source, url)
        for feature in page:
            yield feature
        if len(page) < page_size:
            return
        start += len(page)


async def arcgis_features(
    client: httpx.AsyncClient,
    url: str,
    *,
    source: str,
    out_fields: str,
    where: str = "1=1",
    id_field: str = "OBJECTID",
    page_size: int | None = None,
    extra: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Feições GeoJSON de uma camada ArcGIS, avançando pelo id (nem todo servidor pagina)."""
    last = -1
    while True:
        params: dict[str, Any] = {
            "where": f"({where}) AND {id_field} > {last}",
            "outFields": f"{id_field},{out_fields}",
            "orderByFields": id_field,
            "outSR": 4326,
            "f": "geojson",
            **(extra or {}),
        }
        if page_size is not None:
            params["resultRecordCount"] = page_size
        page = _features(await get_json(client, url, params=params, source=source), source, url)
        if not page:
            return
        for feature in page:
            yield feature
        try:
            last = max(int(feature["properties"][id_field]) for feature in page)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"{source} respondeu feições sem {id_field}.", url=url) from exc
