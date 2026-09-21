"""Hidrografia ANA por escala: principais eixos no país, geometria recortada no viewport.

Área de drenagem determina a hierarquia dos rios; área de superfície filtra lagos.
Os rios nacionais vêm do snapshot local; escalas próximas consultam a ANA em páginas.
"""

from __future__ import annotations

import asyncio
import json
from itertools import pairwise
from pathlib import Path
from typing import Any
from weakref import WeakValueDictionary

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis_cache
from app.core.cache import TTLCache
from app.core.cooldown import SourceCooldown
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
ANA_RIVERS_URL = "https://www.snirh.gov.br/arcgis/rest/services/SNIRH2016/Cursos_Agua_dominialidade/FeatureServer/0/query"
ANA_WATER_BODIES_URL = (
    "https://www.snirh.gov.br/arcgis/rest/services/SNIRH2016/Massa_dagua/MapServer/0/query"
)

# Bounding box aproximado do Brasil (Oeste, Sul, Leste, Norte)
BRAZIL_BBOX: tuple[float, float, float, float] = (-73.99, -33.75, -28.84, 5.27)

CACHE_TTL_SECONDS = 86400  # 24 horas
_cache: TTLCache[HydroFeatureCollection] = TTLCache(CACHE_TTL_SECONDS, max_entries=128)
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
# ANA fora do ar: sem a pausa, cada movimento do mapa esperava o timeout
# inteiro de novo. Durante ela, a camada sai do snapshot local na hora.
ana_cooldown = SourceCooldown("ana", 60)
# Uma resposta parcial vale por pouco tempo: o bastante para não recalcular o
# mesmo recorte, curto o bastante para a ANA voltar logo que se recuperar.
PARTIAL_CACHE_SECONDS = 60

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
            logger.info(
                "Carregados %d rios principais completos pré-consolidados", len(_MAJOR_RIVERS)
            )
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


def hydro_detail(zoom: float) -> tuple[float, float, float]:
    """Área mínima de drenagem, área de lago e tolerância em graus por escala."""
    if zoom < 6:
        return 200000, 250, 0.025
    if zoom < 8:
        return 10000, 25, 0.008
    if zoom < 10:
        return 2000, 2, 0.002
    return 100, 0.2, 0.0005


def _simplify_line(points: list[list[float]], tolerance: float) -> list[list[float]]:
    # Douglas-Peucker iterativo: não depende da profundidade de recursão do rio.
    if len(points) < 3:
        return points
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        ax, ay = points[start][:2]
        bx, by = points[end][:2]
        dx, dy = bx - ax, by - ay
        denom = dx * dx + dy * dy
        greatest, split = tolerance * tolerance, None
        for i in range(start + 1, end):
            x, y = points[i][:2]
            t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / denom)) if denom else 0
            distance = (x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2
            if distance > greatest:
                greatest, split = distance, i
        if split is not None:
            keep.add(split)
            stack.extend([(start, split), (split, end)])
    return [[round(v, 4) for v in points[i][:2]] for i in sorted(keep)]


def _clip_line(
    points: list[list[float]], bbox: tuple[float, float, float, float]
) -> list[list[list[float]]]:
    # Liang-Barsky: mantém apenas os trechos do viewport, inclusive rios que o atravessam.
    west, south, east, north = bbox
    lines: list[list[list[float]]] = []
    for a, b in pairwise(points):
        x, y = a[:2]
        dx, dy = b[0] - x, b[1] - y
        low, high = 0.0, 1.0
        visible = True
        for p, q in ((-dx, x - west), (dx, east - x), (-dy, y - south), (dy, north - y)):
            if p == 0:
                if q < 0:
                    visible = False
                    break
            elif p < 0:
                low = max(low, q / p)
            else:
                high = min(high, q / p)
        if not visible or low > high:
            continue
        begin, end = [x + low * dx, y + low * dy], [x + high * dx, y + high * dy]
        if lines and lines[-1][-1] == begin:
            lines[-1].append(end)
        else:
            lines.append([begin, end])
    return lines


def _visible_river(
    feature: HydroFeature, bbox: tuple[float, float, float, float], tolerance: float
) -> HydroFeature | None:
    geometry = feature.geometry
    lines = (
        geometry["coordinates"]
        if geometry["type"] == "MultiLineString"
        else [geometry["coordinates"]]
    )
    clipped = [part for line in lines for part in _clip_line(_simplify_line(line, tolerance), bbox)]
    if not clipped:
        return None
    return feature.model_copy(
        update={"geometry": {"type": "MultiLineString", "coordinates": clipped}, "bbox": None}
    )


async def _query_features(
    client: httpx.AsyncClient, url: str, params: dict[str, str]
) -> list[dict[str, Any]]:
    # O serviço de massas d'água limita respostas a 1.000 e não oferece offsets.
    # Os IDs selecionados pelo filtro permitem páginas completas, sem baixar o país.
    ids_response = await client.get(
        url, params={**params, "f": "json", "returnGeometry": "false", "returnIdsOnly": "true"}
    )
    ids_response.raise_for_status()
    ids_data = ids_response.json()
    if "objectIds" not in ids_data:
        raise ValueError("ANA não respondeu com os IDs solicitados")
    ids = ids_data["objectIds"] or []
    features = []
    for offset in range(0, len(ids), 250):
        response = await client.get(
            url,
            params={**params, "objectIds": ",".join(str(x) for x in ids[offset : offset + 250])},
        )
        response.raise_for_status()
        data = response.json()
        if "features" not in data or data.get("exceededTransferLimit"):
            raise ValueError("Resposta ANA incompleta")
        features.extend(data["features"])
    return features


async def _fetch_rivers(
    client: httpx.AsyncClient, zoom: float, bbox: tuple[float, float, float, float]
) -> list[HydroFeature]:
    drainage, _, tolerance = hydro_detail(zoom)
    raw = await _query_features(
        client,
        ANA_RIVERS_URL,
        {
            "outFields": "NORIOCOMP,NUAREAMONT,DEDOMINIAL,ORGAO_GEST",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "where": f"NUAREAMONT >= {drainage}",
            "maxAllowableOffset": str(tolerance),
            "geometryPrecision": "4",
            "geometry": _format_bbox(bbox),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        },
    )
    return _consolidate_river_segments(raw)


async def _fetch_water_bodies(
    client: httpx.AsyncClient, zoom: float, bbox: tuple[float, float, float, float]
) -> list[HydroFeature]:
    _, minimum_area, tolerance = hydro_detail(zoom)
    raw = await _query_features(
        client,
        ANA_WATER_BODIES_URL,
        {
            "outFields": "gid,nmoriginal,detipomass,dedominial,nuareakm2",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "where": f"nuareakm2 >= {minimum_area}",
            "maxAllowableOffset": str(tolerance),
            "geometryPrecision": "4",
            "geometry": _format_bbox(bbox),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        },
    )
    features = []
    for item in raw:
        if not item.get("geometry"):
            continue
        props = item["properties"]
        identifier = f"water_body:{props['gid']}"
        features.append(
            HydroFeature(
                id=identifier,
                geometry=item["geometry"],
                properties=HydroFeatureProperties(
                    id=identifier,
                    name=(props.get("nmoriginal") or "").strip() or "Corpo d’água",
                    category="water_body",
                    body_type=props.get("detipomass"),
                    dominion=props.get("dedominial"),
                    area_km2=props.get("nuareakm2"),
                ),
            )
        )
    return features


async def get_hydrography(
    session: AsyncSession,
    *,
    level: str = "country",
    parent_code: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    include_water_bodies: bool = True,
    include_rivers: bool = True,
    zoom: float = 4,
) -> HydroFeatureCollection:
    drainage, _, tolerance = hydro_detail(zoom)
    effective_bbox = bbox
    if effective_bbox is None and parent_code:
        territory = await territories_repo.get_by_code(session, parent_code)
        if territory and territory.bbox:
            effective_bbox = territory.bbox
    effective_bbox = effective_bbox or BRAZIL_BBOX
    key = (
        f"{level}:{parent_code}:{effective_bbox}:{drainage}:{include_water_bodies}:{include_rivers}"
    )
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached
        cached = await redis_cache.read("hydrography", key, HydroFeatureCollection)
        if cached is not None:
            _cache.set(key, cached, ttl_seconds=30)
            return cached
        bodies: list[HydroFeature] = []
        partial = False
        snapshot_rivers = [
            river
            for river in _MAJOR_RIVERS
            if (river.properties.drainage_area_km2 or 0) >= drainage
        ]
        rivers = snapshot_rivers if include_rivers and zoom < 6 else []
        needs_ana = (include_rivers and zoom >= 6) or include_water_bodies
        if needs_ana and ana_cooldown.active:
            partial = True
            if include_rivers and zoom >= 6:
                rivers = snapshot_rivers
        elif needs_ana:
            async with httpx.AsyncClient(
                timeout=30, headers={"User-Agent": "BrasilLens/1.0"}
            ) as client:
                if include_rivers and zoom >= 6:
                    try:
                        rivers = await _fetch_rivers(client, zoom, effective_bbox)
                    except (httpx.HTTPError, ValueError, KeyError):
                        partial = True
                        rivers = snapshot_rivers
                        ana_cooldown.trip()
                        logger.warning("ANA rios indisponível; usando eixos do snapshot")
                if include_water_bodies and not ana_cooldown.active:
                    try:
                        bodies = await _fetch_water_bodies(client, zoom, effective_bbox)
                    except (httpx.HTTPError, ValueError, KeyError):
                        partial = True
                        ana_cooldown.trip()
                        logger.warning("ANA massas de água indisponível")
                elif include_water_bodies:
                    partial = True
        visible = [
            part for river in rivers if (part := _visible_river(river, effective_bbox, tolerance))
        ]
        result = HydroFeatureCollection(
            metadata=HydroMetadata(
                level=level,
                parent_code=parent_code,
                river_count=len(visible),
                water_body_count=len(bodies),
                status="partial" if partial else "ok",
            ),
            bbox=effective_bbox,
            features=visible + bodies,
        )
        # Uma falha transitória não vira uma camada incompleta em cache por 24h.
        if partial:
            _cache.set(key, result, ttl_seconds=PARTIAL_CACHE_SECONDS)
        else:
            _cache.set(key, result)
            await redis_cache.write("hydrography", key, result, CACHE_TTL_SECONDS)
        return result
