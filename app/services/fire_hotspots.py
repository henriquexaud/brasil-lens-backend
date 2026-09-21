"""BDQueimadas oficial: WMS para cobertura completa, WFS para consulta pontual.

Schema confirmado em /queimadas/geoserver/wfs (DescribeFeatureType): os
campos id_0, id_1 e id_2 identificam país, UF e município IBGE, respectivamente.
Não confunde detecções por satélite com incêndios únicos ou fogo ainda ativo.
"""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from typing import Any
from weakref import WeakValueDictionary

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis_cache
from app.core.cache import TTLCache
from app.core.config import settings
from app.core.errors import InvalidParameterError, ProviderError, TerritoryNotFoundError
from app.core.logging import get_logger
from app.repositories import territories
from app.schemas.fire_hotspots import (
    FireHotspotCollection,
    FireHotspotDetails,
    FireHotspotFeature,
    FireHotspotMetadata,
    FireHotspotProperties,
    FirePoint,
    FireScope,
)

logger = get_logger(__name__)
_cache: TTLCache[FireHotspotCollection] = TTLCache(settings.fire_hotspots_cache_ttl_seconds, 64)
_fallback: TTLCache[FireHotspotCollection] = TTLCache(3600, 64)
_failures: TTLCache[bool] = TTLCache(60, 64)
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_FIELDS = (
    "id_foco_bdq,data_hora_gmt,satelite,municipio,estado,id_1,id_2,bioma,"
    "precipitacao,numero_dias_sem_chuva,risco_fogo,frp,geometria"
)
DEFAULT_FIRE_HOURS = 24


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _scope_filter(session: AsyncSession, level: FireScope, parent: str | None) -> str:
    # O país é obrigatório: o serviço também publica detecções fora do Brasil.
    if level == "country":
        if parent is not None:
            raise InvalidParameterError("O recorte Brasil não recebe um território pai.", "parent")
        return "id_0=33"
    expected_length = 2 if level == "state" else 7
    if not parent or len(parent) != expected_length or not parent.isascii() or not parent.isdigit():
        raise InvalidParameterError("Informe o código IBGE correspondente ao recorte.", "parent")
    territory = await territories.get_by_code(session, parent)
    if territory is None:
        raise TerritoryNotFoundError(parent)
    if territory.level.value != level:
        raise InvalidParameterError("O código IBGE não corresponde ao recorte.", "parent")
    field = "id_1" if level == "state" else "id_2"
    return f"id_0=33 AND {field}={int(parent)}"


def _time_filter(scope: str, end: datetime, hours: int) -> str:
    return (
        f"{scope} AND data_hora_gmt >= '{_iso(end - timedelta(hours=hours))}'"
        f" AND data_hora_gmt <= '{_iso(end)}'"
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


def _parse_feature(raw: dict[str, Any]) -> FireHotspotFeature:
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


async def _fetch_wfs(cql_filter: str, count: int) -> tuple[list[FireHotspotFeature], int]:
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
        features = [_parse_feature(feature) for feature in data["features"]]
        if total < len(features) or (total > 0 and not features):
            raise ValueError("Contagem de focos inconsistente")
        return features, total
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ProviderError("Não foi possível consultar os focos de calor no INPE.") from exc


async def get_fire_hotspots(
    session: AsyncSession,
    *,
    level: FireScope = "country",
    parent_code: str | None = None,
    hours: int = DEFAULT_FIRE_HOURS,
) -> FireHotspotCollection:
    scope = await _scope_filter(session, level, parent_code)
    key = f"{level}:{parent_code}:{hours}"
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached
        cached = await redis_cache.read("fire-metadata", key, FireHotspotCollection)
        if cached is not None:
            _cache.set(key, cached, ttl_seconds=30)
            return cached
        try:
            if _failures.get(key):
                raise ProviderError("INPE temporariamente indisponível. Tente em um minuto.")
            end = datetime.now(UTC)
            cql_filter = _time_filter(scope, end, hours)
            # Só a prévia vem em JSON. O WMS não tem esse limite e cobre todos os focos.
            features, total = await _fetch_wfs(cql_filter, 1)
            result = FireHotspotCollection(
                metadata=FireHotspotMetadata(
                    level=level,
                    parent_code=parent_code,
                    hotspot_count=total,
                    hours=hours,
                    fetched_at=datetime.now(UTC),
                    window_start=end - timedelta(hours=hours),
                    window_end=end,
                    latest_detection_at=features[0].properties.detected_at if features else None,
                    wms_url=settings.inpe_queimadas_wms_url,
                    cql_filter=cql_filter,
                ),
                features=features,
            )
            _cache.set(key, result)
            await redis_cache.write(
                "fire-metadata", key, result, settings.fire_hotspots_cache_ttl_seconds
            )
            _fallback.set(key, result)
            await redis_cache.write("fire-fallback", key, result, 3600)
            return result
        except ProviderError:
            if not _failures.get(key):
                _failures.set(key, True)
                logger.warning("fire.inpe_unavailable: %s", key, exc_info=True)
            previous = _fallback.get(key) or await redis_cache.read(
                "fire-fallback", key, FireHotspotCollection
            )
            if previous is not None:
                return previous.model_copy(
                    update={"metadata": previous.metadata.model_copy(update={"status": "stale"})}
                )
            raise


async def identify_fire_hotspots(
    session: AsyncSession,
    *,
    level: FireScope,
    parent_code: str | None,
    hours: int,
    latitude: float,
    longitude: float,
    tolerance: float,
    at: datetime,
) -> FireHotspotDetails:
    if at.tzinfo is None or not -timedelta(minutes=1) <= datetime.now(UTC) - at <= timedelta(
        hours=2
    ):
        raise InvalidParameterError("Atualize a camada antes de consultar este foco.", "at")
    scope = await _scope_filter(session, level, parent_code)
    west, east = max(-180, longitude - tolerance), min(180, longitude + tolerance)
    south, north = max(-90, latitude - tolerance), min(90, latitude + tolerance)
    cql_filter = (
        f"{_time_filter(scope, at, hours)}"
        f" AND BBOX(geometria,{west:.6f},{south:.6f},{east:.6f},{north:.6f},'EPSG:4326')"
    )
    features, total = await _fetch_wfs(cql_filter, 20)
    features.sort(
        key=lambda feature: (
            (feature.geometry.coordinates[0] - longitude) ** 2
            * math.cos(math.radians(latitude)) ** 2
            + (feature.geometry.coordinates[1] - latitude) ** 2
        )
    )
    return FireHotspotDetails(features=features, matched_count=total)
