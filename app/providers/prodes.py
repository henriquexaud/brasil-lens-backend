"""PRODES/INPE por município, a partir dos arquivos do painel do TerraBrasilis.

O painel publica, por bioma, um JSON de áreas desmatadas (km²) por período e
um catálogo de "locais de interesse" em que cada município traz o código IBGE.
São 12 arquivos estáticos lidos uma vez por ingestão, nunca por requisição.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from app.core.errors import ProviderError
from app.providers.base import get_json

DASHBOARD_FILES = "https://terrabrasilis.dpi.inpe.br/app/prodes/dashboard/deforestation/files"
DASHBOARD_URL = (
    "https://terrabrasilis.dpi.inpe.br/app/dashboard/deforestation/biomes/legal_amazon/rates"
)
# O bioma Amazônia (e não a Amazônia Legal) para não contar duas vezes o
# Cerrado e o Pantanal que ficam dentro da Amazônia Legal.
BIOMES: tuple[str, ...] = ("amazon", "cerrado", "caatinga", "mata_atlantica", "pampa", "pantanal")
SOURCE = "INPE/PRODES"


@dataclass(frozen=True, slots=True)
class BiomeIncrements:
    biome: str
    municipalities: frozenset[str]
    # ano PRODES (fim do período agosto–julho) → código IBGE → km²
    annual: Mapping[int, Mapping[str, Decimal]]


@dataclass(frozen=True, slots=True)
class DeforestationRecord:
    ibge_code: str
    reference_year: int
    value: Decimal


def _parse_period(period: Mapping[str, Any]) -> tuple[int, int]:
    start, end = period["startDate"], period["endDate"]
    return int(start["year"]), int(end["year"])


def parse_biome(biome: str, data: Mapping[str, Any], names: Mapping[str, Any]) -> BiomeIncrements:
    try:
        municipal_loi = next(item for item in names["lois"] if item["name"] == "mun")
        codes = {
            int(item["gid"]): str(item["codibge"])
            for item in municipal_loi["loinames"]
            if item.get("codibge")
        }
        loi = int(municipal_loi["gid"])
        annual: dict[int, dict[str, Decimal]] = {}
        for period in data["periods"]:
            start, end = _parse_period(period)
            # Os primeiros períodos de alguns biomas são bienais ou o acumulado
            # até 2000/2007; só o incremento de um ano entra na série anual.
            if end - start != 1:
                continue
            areas = annual.setdefault(end, {})
            for feature in period["features"]:
                if int(feature["loi"]) != loi:
                    continue
                code = codes.get(int(feature["loiname"]))
                if code is None:
                    continue
                area = sum(Decimal(str(item["area"])) for item in feature["areas"])
                areas[code] = areas.get(code, Decimal(0)) + area
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        raise ProviderError(f"Formato inesperado no PRODES ({biome}).", biome=biome) from exc
    if not codes or not annual:
        raise ProviderError(f"PRODES ({biome}) sem municípios ou sem série anual.", biome=biome)
    if any(len(code) != 7 or not code.isdigit() for code in codes.values()):
        raise ProviderError(f"PRODES ({biome}) com código IBGE inválido.", biome=biome)
    return BiomeIncrements(biome=biome, municipalities=frozenset(codes.values()), annual=annual)


def combine(biomes: Iterable[BiomeIncrements]) -> list[DeforestationRecord]:
    """Soma os biomas de cada município, só nos anos que todos os biomas já publicaram.

    O PRODES mapeia o bioma inteiro, então um município listado no bioma e sem
    área no ano teve 0 km² detectados (medição, não ausência). Um ano que um
    dos biomas ainda não publicou não entra: a soma sairia parcial.
    """
    items = list(biomes)
    if not items:
        return []
    years = set.intersection(*(set(item.annual) for item in items))
    totals: dict[tuple[str, int], Decimal] = {}
    for item in items:
        for year in years:
            areas = item.annual[year]
            for code in item.municipalities:
                key = (code, year)
                totals[key] = totals.get(key, Decimal(0)) + areas.get(code, Decimal(0))
    return [
        DeforestationRecord(ibge_code=code, reference_year=year, value=value)
        for (code, year), value in sorted(totals.items())
    ]


async def fetch_biome(client: httpx.AsyncClient, biome: str) -> BiomeIncrements:
    data = await get_json(client, f"{DASHBOARD_FILES}/data/prodes_{biome}.json", source=SOURCE)
    names = await get_json(
        client, f"{DASHBOARD_FILES}/config/loinames/prodes_{biome}.json", source=SOURCE
    )
    return parse_biome(biome, data, names)


async def fetch_last_update(client: httpx.AsyncClient) -> date | None:
    payload = await get_json(client, f"{DASHBOARD_FILES}/last_update_date.json", source=SOURCE)
    try:
        return date.fromisoformat(str(payload["last_date"]))
    except (KeyError, TypeError, ValueError):
        return None
