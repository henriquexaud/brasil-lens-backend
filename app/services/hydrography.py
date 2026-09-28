from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, NamedTuple
from weakref import WeakValueDictionary

import httpx

from app.core import redis_cache
from app.core.cache import TTLCache
from app.core.cooldown import SourceCooldown
from app.core.logging import get_logger
from app.schemas.common import to_json
from app.schemas.hydrography import (
    HydroFeature,
    HydroFeatureCollection,
    HydroFeatureProperties,
    HydroMetadata,
)

logger = get_logger(__name__)

ANA_RIVERS_URL = "https://www.snirh.gov.br/arcgis/rest/services/SNIRH2016/Cursos_Agua_dominialidade/FeatureServer/0/query"
ANA_WATER_BODIES_URL = (
    "https://www.snirh.gov.br/arcgis/rest/services/SNIRH2016/Massa_dagua/MapServer/0/query"
)

BRAZIL_BBOX: tuple[float, float, float, float] = (-73.99, -33.75, -28.84, 5.27)


class HydroResponse(NamedTuple):
    """JSON pronto da resposta: 128 áreas ocupavam ~375 MB como modelo e ~43 MB assim."""

    body: bytes
    status: Literal["ok", "partial"]


CACHE_TTL_SECONDS = 86400
_cache: TTLCache[HydroResponse] = TTLCache(CACHE_TTL_SECONDS, max_entries=128)
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
ana_cooldown = SourceCooldown("ana", 60)
PARTIAL_CACHE_SECONDS = 60

_SNAPSHOT_PATH = Path(__file__).parent / "data" / "major_rivers.json"
_MAJOR_RIVERS: list[HydroFeature] = []


def _init_major_rivers() -> list[HydroFeature]:
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
                    geometry={
                        "type": "MultiLineString",
                        "coordinates": _merge_lines(item["geometry"]["coordinates"]),
                    },
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


def _merge_lines(lines: list[list[list[float]]]) -> list[list[list[float]]]:
    """Emenda os trechos em que um começa onde o outro termina.

    A ANA entrega cada rio picado em trechos entre confluências; emendados, o mesmo
    traçado sai com uma fração das partes e sem repetir o ponto de cada junção.
    """
    lines = [line for line in lines if line]
    by_start: dict[tuple[float, ...], list[int]] = {}
    for index, line in enumerate(lines):
        by_start.setdefault(tuple(line[0]), []).append(index)
    continued = {tuple(line[-1]) for line in lines}
    used: set[int] = set()
    merged: list[list[list[float]]] = []
    # Cabeceiras primeiro, para cada cadeia começar no trecho mais a montante.
    for index in sorted(range(len(lines)), key=lambda i: tuple(lines[i][0]) in continued):
        if index in used:
            continue
        used.add(index)
        chain = list(lines[index])
        while True:
            following = [i for i in by_start.get(tuple(chain[-1]), ()) if i not in used]
            if not following:
                break
            used.add(following[0])
            chain.extend(lines[following[0]][1:])
        merged.append(chain)
    return merged


_init_major_rivers()


def _format_bbox(bbox: tuple[float, float, float, float]) -> str:
    west, south, east, north = bbox
    return f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}"


def _consolidate_river_segments(raw_features: list[dict[str, Any]]) -> list[HydroFeature]:
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
                geometry={"type": "MultiLineString", "coordinates": _merge_lines(entry["lines"])},
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
    if zoom < 6:
        return 200000, 250, 0.025
    if zoom < 8:
        return 10000, 25, 0.008
    if zoom < 10:
        return 2000, 2, 0.002
    return 100, 0.2, 0.0005


def _simplify_line(points: list[list[float]], tolerance: float) -> list[list[float]]:
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


def _ring_area(ring: list[list[float]]) -> float:
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in pairwise(ring))) / 2


def _visible_rings(geometry: dict[str, Any], min_area: float) -> dict[str, Any] | None:
    """Tira lagoas, ilhas e buracos com poucos pixels nesse detalhe.

    Nas escalas regional e nacional, esses anéis eram metade dos pontos da camada.
    """
    polygons = (
        [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    )
    kept = [
        [outer, *(hole for hole in holes if _ring_area(hole) >= min_area)]
        for outer, *holes in polygons
        if _ring_area(outer) >= min_area
    ]
    if not kept:
        return None
    if len(kept) == 1:
        return {"type": "Polygon", "coordinates": kept[0]}
    return {"type": "MultiPolygon", "coordinates": kept}


async def _query_features(
    client: httpx.AsyncClient, url: str, params: dict[str, str]
) -> list[dict[str, Any]]:
    ids_response = await client.get(
        url, params={**params, "f": "json", "returnGeometry": "false", "returnIdsOnly": "true"}
    )
    ids_response.raise_for_status()
    ids_data = ids_response.json()
    if "objectIds" not in ids_data:
        raise ValueError("ANA não respondeu com os IDs solicitados")
    ids = ids_data["objectIds"] or []
    # Os lotes saem em paralelo; o pool do cliente limita quantos vão à ANA de cada vez.
    responses = await asyncio.gather(
        *(
            client.get(
                url,
                params={
                    **params,
                    "objectIds": ",".join(str(x) for x in ids[offset : offset + 250]),
                },
            )
            for offset in range(0, len(ids), 250)
        )
    )
    features = []
    for response in responses:
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
        geometry = item.get("geometry") and _visible_rings(item["geometry"], (4 * tolerance) ** 2)
        if not geometry:
            continue
        props = item["properties"]
        identifier = f"water_body:{props['gid']}"
        features.append(
            HydroFeature(
                id=identifier,
                geometry=geometry,
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


async def _or_none(fetch: Coroutine[Any, Any, list[HydroFeature]]) -> list[HydroFeature] | None:
    try:
        return await fetch
    except (httpx.HTTPError, ValueError, KeyError):
        logger.warning("hydrography.ana_unavailable", exc_info=True)
        return None


async def get_hydrography(
    *, zoom: float = 4, bbox: tuple[float, float, float, float] | None = None
) -> HydroResponse:
    drainage, _, tolerance = hydro_detail(zoom)
    area = bbox if zoom >= 6 and bbox else BRAZIL_BBOX
    key = f"{drainage}:{area}"
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached
        stored = await redis_cache.read("hydrography", key, HydroFeatureCollection)
        if stored is not None:
            cached = HydroResponse(to_json(stored), stored.metadata.status)
            _cache.set(key, cached, ttl_seconds=30)
            return cached
        snapshot = [
            river
            for river in _MAJOR_RIVERS
            if (river.properties.drainage_area_km2 or 0) >= drainage
        ]
        rivers: list[HydroFeature] | None = snapshot
        bodies: list[HydroFeature] | None = None
        if not ana_cooldown.active:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30, pool=None),
                limits=httpx.Limits(max_connections=4),
                headers={"User-Agent": "BrasilLens/1.0"},
            ) as client:
                fetches = [_or_none(_fetch_water_bodies(client, zoom, area))]
                if zoom >= 6:
                    fetches.append(_or_none(_fetch_rivers(client, zoom, area)))
                bodies, *ana_rivers = await asyncio.gather(*fetches)
            rivers = ana_rivers[0] if ana_rivers else snapshot
            if bodies is None or rivers is None:
                ana_cooldown.trip()
        partial = bodies is None or rivers is None
        if rivers is None:
            rivers = snapshot
        bodies = bodies or []
        visible = [part for river in rivers if (part := _visible_river(river, area, tolerance))]
        result = HydroFeatureCollection(
            metadata=HydroMetadata(
                river_count=len(visible),
                water_body_count=len(bodies),
                status="partial" if partial else "ok",
            ),
            bbox=area,
            features=visible + bodies,
        )
        response = HydroResponse(to_json(result), result.metadata.status)
        if partial:
            _cache.set(key, response, ttl_seconds=PARTIAL_CACHE_SECONDS)
        else:
            _cache.set(key, response)
            await redis_cache.write("hydrography", key, result, CACHE_TTL_SECONDS)
        return response


async def warm_up() -> None:
    try:
        await get_hydrography()
    except Exception:
        logger.warning("hydrography.warm_up_failed", exc_info=True)
