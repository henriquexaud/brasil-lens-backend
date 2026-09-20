"""Serviço de hidrografia: rios completos e prioritários com carregamento ultra rápido.

Consome e consolida dados da Agência Nacional de Águas e Saneamento Básico (ANA) / SNIRH.
Principais recursos:
- Priorização de rios completos (nascente até foz) em vez de trechos desconectados;
- Carregamento instantâneo (< 5ms) dos 64 maiores rios estruturantes do Brasil via snapshot pré-consolidado;
- Consolidação dinâmica de trechos em MultiLineString contínua por nome de rio;
- Filtragem espacial por bounding box derivado dos territórios oficiais do IBGE;
- Cache com TTL longo (24h) e controle de concorrência com asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from weakref import WeakValueDictionary

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.core.logging import get_logger
from app.repositories import territories as territories_repo
from app.schemas.hydrography import (
    HydroFeature,
    HydroFeatureCollection,
    HydroFeatureProperties,
    HydroMetadata,
)

logger = get_logger(__name__)

# Endpoints oficiais da ANA / SNIRH
ANA_RIVERS_URL = (
    "https://www.snirh.gov.br/arcgis/rest/services/SNIRH2016/Cursos_Agua_dominialidade/FeatureServer/0/query"
)
ANA_WATER_BODIES_URL = (
    "https://www.snirh.gov.br/arcgis/rest/services/SNIRH2016/Massa_dagua/MapServer/0/query"
)

# Bounding box aproximado do Brasil (Oeste, Sul, Leste, Norte)
BRAZIL_BBOX: tuple[float, float, float, float] = (-73.99, -33.75, -28.84, 5.27)

CACHE_TTL_SECONDS = 86400  # 24 horas
_cache: TTLCache[HydroFeatureCollection] = TTLCache(CACHE_TTL_SECONDS, max_entries=128)
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

_SNAPSHOT_PATH = Path(__file__).parent / "data" / "major_rivers.json"
_MAJOR_RIVERS: list[HydroFeature] = []


def _init_major_rivers() -> list[HydroFeature]:
    """Carrega os 64 rios principais completos pré-consolidados em memória."""
    global _MAJOR_RIVERS
    if _MAJOR_RIVERS:
        return _MAJOR_RIVERS

    if _SNAPSHOT_PATH.exists():
        try:
            with open(_SNAPSHOT_PATH, encoding="utf-8") as f:
                raw_list = json.load(f)
            features: list[HydroFeature] = []
            for item in raw_list:
                props = item.get("properties", {})
                feat = HydroFeature(
                    id=item.get("id", f"river:{props.get('name', 'rio')}"),
                    geometry=item.get("geometry", {}),
                    properties=HydroFeatureProperties(
                        id=props.get("id", ""),
                        name=props.get("name", "Rio"),
                        category="river",
                        drainage_area_km2=props.get("drainageAreaKm2"),
                        dominion=props.get("dominion"),
                        management=props.get("management"),
                        segment_count=props.get("segmentCount"),
                    ),
                    bbox=tuple(item["bbox"]) if "bbox" in item else None,
                )
                features.append(feat)
            features.sort(
                key=lambda x: x.properties.drainage_area_km2 or 0.0,
                reverse=True,
            )
            _MAJOR_RIVERS = features
            logger.info("Carregados %d rios principais completos pré-consolidados", len(_MAJOR_RIVERS))
        except Exception as exc:
            logger.warning("Falha ao carregar snapshot de rios principais: %s", exc)

    return _MAJOR_RIVERS


# Inicialização imediata ao carregar o módulo
_init_major_rivers()


def _format_bbox(bbox: tuple[float, float, float, float]) -> str:
    west, south, east, north = bbox
    return f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}"


def _consolidate_river_segments(raw_features: list[dict[str, Any]]) -> list[HydroFeature]:
    """Agrupa segmentos avulsos pelo nome do rio e consolida em MultiLineString contínua."""
    grouped: dict[str, dict[str, Any]] = {}
    for feat in raw_features:
        geom = feat.get("geometry")
        if not geom or not geom.get("coordinates"):
            continue

        props = feat.get("properties", {})
        name = (props.get("NORIOCOMP") or "").strip()
        if not name:
            name = "Curso d'água sem nome"

        area_raw = props.get("NUAREAMONT")
        area = float(area_raw) if area_raw is not None else 0.0

        if name not in grouped:
            grouped[name] = {
                "name": name,
                "max_area": area,
                "dominion": props.get("DEDOMINIAL"),
                "management": props.get("ORGAO_GEST"),
                "lines": [],
                "segment_count": 0,
            }

        entry = grouped[name]
        entry["segment_count"] += 1
        if area > entry["max_area"]:
            entry["max_area"] = area
            if props.get("DEDOMINIAL"):
                entry["dominion"] = props.get("DEDOMINIAL")
            if props.get("ORGAO_GEST"):
                entry["management"] = props.get("ORGAO_GEST")

        coords = geom["coordinates"]
        if geom["type"] == "LineString":
            entry["lines"].append(coords)
        elif geom["type"] == "MultiLineString":
            entry["lines"].extend(coords)

    results: list[HydroFeature] = []
    for name, entry in grouped.items():
        slug = (
            name.lower()
            .replace(" ", "-")
            .replace("ã", "a")
            .replace("á", "a")
            .replace("é", "e")
            .replace("í", "i")
            .replace("ó", "o")
            .replace("ú", "u")
            .replace("ç", "c")
        )
        feat_id = f"river:{slug}"
        results.append(
            HydroFeature(
                id=feat_id,
                geometry={"type": "MultiLineString", "coordinates": entry["lines"]},
                properties=HydroFeatureProperties(
                    id=feat_id,
                    name=name,
                    category="river",
                    drainage_area_km2=round(entry["max_area"], 1),
                    dominion=entry["dominion"],
                    management=entry["management"],
                    segment_count=entry["segment_count"],
                ),
            )
        )

    results.sort(key=lambda x: x.properties.drainage_area_km2 or 0.0, reverse=True)
    return results


async def _fetch_rivers(
    client: httpx.AsyncClient,
    level: str,
    bbox: tuple[float, float, float, float],
) -> list[HydroFeature]:
    """Consulta cursos d'água na ANA e os consolida em rios inteiros."""
    params: dict[str, str] = {
        "outFields": "NORIOCOMP,NUAREAMONT,DEDOMINIAL,ORGAO_GEST",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }

    if level == "country":
        params["where"] = "NUAREAMONT >= 60000"
        params["maxAllowableOffset"] = "0.01"
    elif level == "state":
        params["where"] = "NUAREAMONT >= 2500"
        params["maxAllowableOffset"] = "0.005"
        params["geometry"] = _format_bbox(bbox)
        params["geometryType"] = "esriGeometryEnvelope"
        params["inSR"] = "4326"
        params["spatialRel"] = "esriSpatialRelIntersects"
    else:  # municipality / local
        params["where"] = "NUAREAMONT >= 30"
        params["maxAllowableOffset"] = "0.001"
        params["geometry"] = _format_bbox(bbox)
        params["geometryType"] = "esriGeometryEnvelope"
        params["inSR"] = "4326"
        params["spatialRel"] = "esriSpatialRelIntersects"

    try:
        response = await client.get(ANA_RIVERS_URL, params=params, timeout=12.0)
        if response.status_code != 200:
            logger.warning(
                "ANA rios retornou HTTP %s: %s",
                response.status_code,
                response.text[:200],
            )
            return []

        data: dict[str, Any] = response.json()
        raw_features = data.get("features", [])
        return _consolidate_river_segments(raw_features)
    except Exception as exc:
        logger.warning("Falha ao consultar rios na ANA (%s): %s", level, exc)
        return []


async def _fetch_water_bodies(
    client: httpx.AsyncClient,
    level: str,
    bbox: tuple[float, float, float, float],
) -> list[HydroFeature]:
    """Consulta lagos, represas e reservatórios na ANA."""
    params: dict[str, str] = {
        "outFields": "nmoriginal,detipomass,dedominial",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }

    if level == "country":
        params["where"] = "nmoriginal IS NOT NULL AND nmoriginal <> ' '"
        params["maxAllowableOffset"] = "0.015"
    elif level == "state":
        params["where"] = "nmoriginal IS NOT NULL AND nmoriginal <> ' '"
        params["maxAllowableOffset"] = "0.005"
        params["geometry"] = _format_bbox(bbox)
        params["geometryType"] = "esriGeometryEnvelope"
        params["inSR"] = "4326"
        params["spatialRel"] = "esriSpatialRelIntersects"
    else:
        params["where"] = "1=1"
        params["maxAllowableOffset"] = "0.001"
        params["geometry"] = _format_bbox(bbox)
        params["geometryType"] = "esriGeometryEnvelope"
        params["inSR"] = "4326"
        params["spatialRel"] = "esriSpatialRelIntersects"

    try:
        response = await client.get(ANA_WATER_BODIES_URL, params=params, timeout=3.5)
        if response.status_code != 200:
            return []

        data: dict[str, Any] = response.json()
        features_raw = data.get("features", [])
        features: list[HydroFeature] = []

        for idx, feat in enumerate(features_raw):
            geom = feat.get("geometry")
            if not geom or not geom.get("coordinates"):
                continue

            props = feat.get("properties", {})
            name = (props.get("nmoriginal") or "").strip()
            if not name:
                name = "Massa d'água"

            feat_id = f"water_body:{props.get('gid', idx)}"
            features.append(
                HydroFeature(
                    id=feat_id,
                    geometry=geom,
                    properties=HydroFeatureProperties(
                        id=feat_id,
                        name=name,
                        category="water_body",
                        body_type=props.get("detipomass"),
                        dominion=props.get("dedominial"),
                    ),
                )
            )

        return features
    except Exception:
        return []


async def get_hydrography(
    session: AsyncSession,
    *,
    level: str = "country",
    parent_code: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    include_water_bodies: bool = True,
) -> HydroFeatureCollection:
    """Retorna hidrografia com rios completos, priorizados por porte e com resposta instantânea."""
    effective_level = level.lower()
    effective_parent = parent_code.strip() if parent_code else None

    cache_key = f"{effective_level}:{effective_parent}:{bbox}:{include_water_bodies}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    lock = _locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached

        # 1. Visão Geral do País (Brasil): entrega imediata dos 64 rios principais completos
        if effective_level == "country" and not effective_parent and not bbox:
            rivers = list(_MAJOR_RIVERS)
            water_bodies: list[HydroFeature] = []
            if include_water_bodies:
                async with httpx.AsyncClient(
                    headers={"User-Agent": "BrasilLens/1.0"},
                    verify=False,
                ) as client:
                    water_bodies = await _fetch_water_bodies(client, "country", BRAZIL_BBOX)

            result = HydroFeatureCollection(
                metadata=HydroMetadata(
                    level="country",
                    parent_code=None,
                    river_count=len(rivers),
                    water_body_count=len(water_bodies),
                ),
                bbox=BRAZIL_BBOX,
                features=rivers + water_bodies,
            )
            _cache.set(cache_key, result)
            return result

        # 2. Resolução de bounding box para estado ou município
        effective_bbox = bbox
        if effective_bbox is None and effective_parent:
            territory = await territories_repo.get_by_code(session, effective_parent)
            if territory and territory.bbox:
                effective_bbox = territory.bbox

        if effective_bbox is None:
            effective_bbox = BRAZIL_BBOX

        sw, ss, se, sn = effective_bbox

        # Encontra rios estruturantes que tocam o recorte (mantidos COMPLETOS)
        major_rivers_in_scope: list[HydroFeature] = []
        major_names: set[str] = set()
        for river in _MAJOR_RIVERS:
            if river.bbox:
                rw, rs, re, rn = river.bbox
                if not (re < sw or rw > se or rn < ss or rs > sn):
                    major_rivers_in_scope.append(river)
                    major_names.add(river.properties.name)

        # Consulta rios regionais e corpos d'água complementares na ANA
        async with httpx.AsyncClient(
            headers={"User-Agent": "BrasilLens/1.0"},
            verify=False,
        ) as client:
            rivers_task = _fetch_rivers(client, effective_level, effective_bbox)
            if include_water_bodies:
                wb_task = _fetch_water_bodies(client, effective_level, effective_bbox)
                dynamic_rivers, water_bodies = await asyncio.gather(rivers_task, wb_task)
            else:
                dynamic_rivers = await rivers_task
                water_bodies = []

        # Mescla: rios completos prioritários + novos rios consolidados
        merged_rivers: list[HydroFeature] = list(major_rivers_in_scope)
        for dyn in dynamic_rivers:
            if dyn.properties.name not in major_names:
                merged_rivers.append(dyn)
                major_names.add(dyn.properties.name)

        # Priorização baseada nos rios maiores (ordenação por área de drenagem decrescente)
        merged_rivers.sort(key=lambda x: x.properties.drainage_area_km2 or 0.0, reverse=True)

        result = HydroFeatureCollection(
            metadata=HydroMetadata(
                level=effective_level,
                parent_code=effective_parent,
                river_count=len(merged_rivers),
                water_body_count=len(water_bodies),
            ),
            bbox=effective_bbox,
            features=merged_rivers + water_bodies,
        )

        _cache.set(cache_key, result)
        return result
