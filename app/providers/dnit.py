"""Rodovias federais do Sistema Nacional de Viação (SNV), pelo GeoServer do DNIT.

A camada `vgeo:vw_snv_rod` guarda todas as versões do SNV; a ingestão usa só a
mais recente (`Versao_SNV`, como `202610A`). Trechos planejados, em obras de
implantação e travessias por balsa não são rodovia existente e ficam de fora.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from app.core.errors import ProviderError
from app.providers.base import get_json
from app.providers.bc250 import TransportFeature
from app.providers.shapefile import wkb_multilinestring

SOURCE = "DNIT"
WFS_URL = "https://servicos.dnit.gov.br/dnitgeo/geoserver/ows"
DATASET_URL = "https://www.gov.br/dnit/pt-br/assuntos/atlas-e-mapas/pnv-e-snv"
LAYER = "vgeo:vw_snv_rod"
PAGE_SIZE = 2000

# Códigos de superfície do SNV.
SURFACES = {
    "DUP": "Duplicada",
    "EOD": "Em obras de duplicação",
    "PAV": "Pavimentada",
    "EOP": "Em obras de pavimentação",
    "IMP": "Implantada (sem pavimento)",
    "LEN": "Leito natural",
}
_NOT_EXISTING = frozenset({"PLA", "EOI", "TRV"})


def _params(**extra: Any) -> dict[str, Any]:
    return {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": LAYER,
        "outputFormat": "application/json",
        **extra,
    }


async def latest_version(client: httpx.AsyncClient) -> str:
    payload = await get_json(
        client,
        WFS_URL,
        params=_params(count=1, sortBy="Versao_SNV D", propertyName="Versao_SNV"),
        source=SOURCE,
    )
    try:
        version = str(payload["features"][0]["properties"]["Versao_SNV"])
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("DNIT respondeu o SNV sem versão.", url=WFS_URL) from exc
    if not version.strip():
        raise ProviderError("DNIT respondeu o SNV sem versão.", url=WFS_URL)
    return version


async def fetch_roads(client: httpx.AsyncClient, version: str) -> list[dict[str, Any]]:
    """Todos os trechos da versão, em páginas sequenciais (o GeoServer é lento)."""
    features: list[dict[str, Any]] = []
    while True:
        payload = await get_json(
            client,
            WFS_URL,
            params=_params(
                count=PAGE_SIZE,
                startIndex=len(features),
                sortBy="id_snv",
                srsName="EPSG:4326",
                cql_filter=f"Versao_SNV='{version}'",
            ),
            source=SOURCE,
        )
        page = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(page, list):
            raise ProviderError("DNIT respondeu o SNV num formato inesperado.", url=WFS_URL)
        features.extend(page)
        if len(page) < PAGE_SIZE:
            return features


def _parts(geometry: dict[str, Any] | None) -> list[list[list[float]]]:
    if not geometry:
        return []
    if geometry.get("type") == "LineString":
        lines = [geometry.get("coordinates") or []]
    elif geometry.get("type") == "MultiLineString":
        lines = geometry.get("coordinates") or []
    else:
        raise ProviderError(f"Trecho do SNV com geometria inesperada: {geometry.get('type')}.")
    return [[point[:2] for point in line] for line in lines if len(line) >= 2]


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def roads(features: list[dict[str, Any]]) -> Iterator[TransportFeature]:
    for feature in features:
        properties = feature.get("properties") or {}
        surface = _text(properties.get("Superficie_Federal"))
        if surface in _NOT_EXISTING:
            continue
        parts = _parts(feature.get("geometry"))
        if not parts:
            continue
        code = _text(properties.get("Codigo_BR"))
        start = _text(properties.get("Local_Inicio"))
        end = _text(properties.get("Local_Fim"))
        attributes = {
            "snv": _text(properties.get("Codigo_SNV")),
            "surface": SURFACES.get(surface or "", surface),
            "administration": _text(properties.get("Administracao")),
            "stretch": f"{start} – {end}" if start and end else None,
        }
        yield TransportFeature(
            layer="road_federal",
            name=f"BR-{code}" if code else None,
            geometry=wkb_multilinestring(parts),
            attributes={key: value for key, value in attributes.items() if value},
        )
