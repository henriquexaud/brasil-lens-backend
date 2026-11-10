"""Registros de ocorrência de espécies do GBIF (Global Biodiversity Information Facility).

O SiBBr, do MCTI, é o nó brasileiro da rede e publica nela os dados das instituições
do país. O recorte usa as divisões administrativas do próprio GBIF (GADM), achadas
por um ponto dentro do território.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.cooldown import SourceCooldown
from app.core.errors import ProviderError

SOURCE = "GBIF"
DATASET_URL = "https://www.gbif.org/country/BR/summary"
gbif_cooldown = SourceCooldown("gbif", 60)

THREAT_CATEGORIES = ("CR", "EN", "VU")


@dataclass(frozen=True, slots=True)
class SpeciesName:
    scientific_name: str
    family: str | None
    common_name: str | None


async def _get(client: httpx.AsyncClient, path: str, params: Any = None) -> Any:
    try:
        response = await client.get(path, params=params)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        gbif_cooldown.trip()
        raise ProviderError(f"Falha ao consultar o {SOURCE}: {exc}", url=path) from exc


async def administrative_units(
    client: httpx.AsyncClient, latitude: float, longitude: float
) -> list[tuple[str, str, str]]:
    """Unidades GADM no ponto e ao redor dele: `(nível, id, nome)`, como `("GADM2", …)`."""
    payload = await _get(client, "/geocode/reverse", {"lat": latitude, "lng": longitude})
    if not isinstance(payload, list):
        raise ProviderError(f"{SOURCE} respondeu o geocódigo num formato inesperado.")
    return [
        (str(item.get("type")), str(item.get("id")), str(item.get("title")))
        for item in payload
        if isinstance(item, dict) and str(item.get("type", "")).startswith("GADM")
    ]


async def species_counts(
    client: httpx.AsyncClient,
    area: dict[str, str],
    *,
    taxon_keys: tuple[int, ...] = (),
    threat: str | None = None,
    limit: int,
) -> tuple[int, list[tuple[int, int]]]:
    """Total de registros e as espécies com mais registros: `(chave, registros)`."""
    params: list[tuple[str, str | int]] = [
        *area.items(),
        ("occurrenceStatus", "PRESENT"),
        ("limit", 0),
        ("facet", "speciesKey"),
        ("facetLimit", limit),
    ]
    params += [("taxonKey", key) for key in taxon_keys]
    if threat:
        params.append(("iucnRedListCategory", threat))
    payload = await _get(client, "/occurrence/search", params)
    try:
        counts = payload["facets"][0]["counts"] if payload["facets"] else []
        return int(payload["count"]), [(int(item["name"]), int(item["count"])) for item in counts]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProviderError(f"{SOURCE} respondeu os registros num formato inesperado.") from exc


def _common_name(payload: Any) -> str | None:
    names = payload.get("results") if isinstance(payload, dict) else None
    portuguese = [
        item
        for item in names or []
        if isinstance(item, dict) and item.get("language") == "por" and item.get("vernacularName")
    ]
    if not portuguese:
        return None
    # Nome usado no Brasil primeiro; sem ele, o primeiro em português.
    chosen = next((item for item in portuguese if item.get("country") == "BR"), portuguese[0])
    name = " ".join(str(chosen["vernacularName"]).split())
    return name[:1].upper() + name[1:] if name else None


async def species_name(client: httpx.AsyncClient, key: int) -> SpeciesName:
    taxon = await _get(client, f"/species/{key}")
    names = await _get(client, f"/species/{key}/vernacularNames", {"limit": 200})
    if not isinstance(taxon, dict) or not (
        taxon.get("canonicalName") or taxon.get("scientificName")
    ):
        raise ProviderError(f"{SOURCE} respondeu a espécie {key} sem nome.")
    return SpeciesName(
        scientific_name=str(taxon.get("canonicalName") or taxon["scientificName"]),
        family=str(taxon["family"]) if taxon.get("family") else None,
        common_name=_common_name(names),
    )
