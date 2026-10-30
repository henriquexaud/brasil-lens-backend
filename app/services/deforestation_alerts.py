import asyncio
import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from weakref import WeakValueDictionary

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis_cache
from app.core.cache import TTLCache
from app.core.errors import ProviderError
from app.providers import deter
from app.repositories.boundaries import municipality_areas, state_areas
from app.repositories.map_projection import data_version
from app.schemas.deforestation_alerts import AlertMunicipality, AlertPoint, DeforestationAlerts

# O INPE publica o DETER com ~2 semanas de atraso (a janela de 48 h dos focos ficaria vazia),
# então o corte é relativo ao último alerta publicado, não a hoje.
WINDOW_DAYS = 14
LOOKBACK_DAYS = 30
PARTIAL_TTL = 300
_cache: TTLCache[DeforestationAlerts] = TTLCache(7200, 4)
_failures: TTLCache[bool] = TTLCache(60, 4)
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _valid(area: float | None) -> bool:
    return area is not None and math.isfinite(area) and area > 0


def aggregate(
    alerts: list[deter.Alert],
    areas: list[dict[str, Any]],
    states: list[dict[str, Any]],
    fetched_at: datetime,
    days: int = WINDOW_DAYS,
    biomes: list[str] | None = None,
) -> DeforestationAlerts:
    every = [name for name, _, _ in deter.BIOMES]
    biomes = every if biomes is None else biomes
    partial = len(biomes) < len(every)
    if not alerts:
        return DeforestationAlerts(
            window_start=None,
            window_end=None,
            days=days,
            fetched_at=fetched_at,
            total=0,
            total_km2=0.0,
            biomes=biomes,
            partial=partial,
            municipalities=[],
            states=[],
            ranked_municipalities=[],
            points=[],
        )
    end = max(alert.view_date for alert in alerts)
    start = end - timedelta(days=days - 1)
    recent = [alert for alert in alerts if alert.view_date >= start]
    by_name = {(deter.normalize(area["name"]), area["state"]): area["ibge_code"] for area in areas}
    km2: defaultdict[str, float] = defaultdict(float)
    counts: defaultdict[str, int] = defaultdict(int)
    latest: dict[str, date] = {}
    unassigned = 0
    coded = [
        (alert, alert.ibge_code or by_name.get((deter.normalize(alert.municipality), alert.state)))
        for alert in recent
    ]
    for alert, code in coded:
        if code is None:
            unassigned += 1
            continue
        for scope in (code, code[:2]):
            km2[scope] += alert.area_km2
            counts[scope] += 1
            latest[scope] = max(latest.get(scope, alert.view_date), alert.view_date)

    def build(area: dict[str, Any]) -> AlertMunicipality:
        code = area["ibge_code"]
        size = area["area_km2"]
        return AlertMunicipality(
            ibge_code=code,
            name=area["name"],
            state=area["state"],
            area_km2=round(size, 3) if _valid(size) else None,
            count=counts[code],
            alert_km2=round(km2[code], 4),
            density=round(km2[code] * 1000 / size, 3) if _valid(size) else None,
            latest_view_date=latest.get(code),
        )

    municipalities = [build(area) for area in areas if counts[area["ibge_code"]]]
    ranked = sorted(
        (item for item in municipalities if item.density is not None),
        key=lambda item: item.density or 0.0,
        reverse=True,
    )[:5]
    return DeforestationAlerts(
        window_start=start,
        window_end=end,
        days=days,
        fetched_at=fetched_at,
        total=len(recent),
        total_km2=round(sum(alert.area_km2 for alert in recent), 3),
        unassigned_count=unassigned,
        biomes=biomes,
        partial=partial,
        municipalities=municipalities,
        states=[build(area) for area in states if counts[area["ibge_code"]]],
        ranked_municipalities=ranked,
        points=[
            AlertPoint(
                ibge_code=code,
                longitude=alert.longitude,
                latitude=alert.latitude,
                area_km2=round(alert.area_km2, 4),
                view_date=alert.view_date,
                municipality=alert.municipality,
                state=alert.state,
                biome=alert.biome,
            )
            for alert, code in coded
        ],
    )


async def get_alerts(session: AsyncSession) -> DeforestationAlerts:
    now = datetime.now(UTC)
    version = await data_version(session)
    key = f"{now.date().isoformat()}:{WINDOW_DAYS}:{version}"
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached
        cached = await redis_cache.read("deforestation-alerts", key, DeforestationAlerts)
        if cached is not None:
            _cache.set(key, cached, ttl_seconds=30)
            return cached
        if _failures.get(key):
            raise ProviderError("Alertas do INPE temporariamente indisponíveis.")
        try:
            alerts, answered = await deter.fetch_alerts(now.date() - timedelta(days=LOOKBACK_DAYS))
        except ProviderError:
            _failures.set(key, True)
            raise
        result = aggregate(
            alerts,
            await municipality_areas(session),
            await state_areas(session),
            now,
            biomes=answered,
        )
        # Resposta parcial vale 5 min, para o bioma que falhou voltar logo.
        ttl = PARTIAL_TTL if result.partial else None
        _cache.set(key, result, ttl_seconds=ttl)
        await redis_cache.write("deforestation-alerts", key, result, ttl or 7200)
        return result
