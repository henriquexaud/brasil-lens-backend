"""Agregação completa do WFS: CSV compacto paginado, cache e nenhuma geometria por foco.

Contagens e densidades estaduais e municipais usam os registros
originais e a área geodésica da malha canônica, nunca o tamanho da geometria simplificada.
"""

import asyncio
import csv
import io
import math
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from weakref import WeakValueDictionary

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis_cache
from app.core.cache import TTLCache
from app.core.config import settings
from app.core.errors import InvalidParameterError, ProviderError
from app.repositories.fire import municipality_areas, state_areas
from app.schemas.fire_hotspots import FireMunicipality, FireScope, FireSummary
from app.services.fire_hotspots import _fetch_wfs, _scope_filter, _time_filter

_cache: TTLCache[FireSummary] = TTLCache(7200, 32)
_failures: TTLCache[bool] = TTLCache(60, 32)
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
PAGE_SIZE = 10000


async def _fetch_rows(cql: str, total: int) -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=settings.inpe_queimadas_http_timeout) as client:
        rows: list[dict[str, str]] = []
        # Sequencial: limita pressão no serviço público e memória, sem truncar páginas.
        while len(rows) < total:
            response = await client.get(
                settings.inpe_queimadas_wfs_url,
                params={
                    "service": "WFS",
                    "version": "2.0.0",
                    "request": "GetFeature",
                    "typeNames": "bdqueimadas:focos",
                    "outputFormat": "csv",
                    "propertyName": "id_foco_bdq,id_1,id_2,data_hora_gmt",
                    "cql_filter": cql,
                    "count": min(PAGE_SIZE, total - len(rows)),
                    "startIndex": len(rows),
                    "sortBy": "data_hora_gmt D,id_foco_bdq D",
                },
            )
            response.raise_for_status()
            page = list(csv.DictReader(io.StringIO(response.text)))
            if not page or "id_foco_bdq" not in page[0]:
                raise ValueError("Página de focos incompleta")
            rows.extend(page)
        if len(rows) != total or len({row["id_foco_bdq"] for row in rows}) != total:
            raise ValueError("Contagem de focos mudou durante a consulta; tente novamente")
        return rows


def aggregate(
    rows: list[dict[str, str]],
    areas: list[dict[str, Any]],
    end: datetime,
    hours: int,
    states: list[dict[str, Any]] | None = None,
) -> FireSummary:
    counts: Counter[str] = Counter()
    recent: Counter[str] = Counter()
    latest: dict[str, datetime] = {}
    for row in rows:
        code = row["id_2"]
        detected = datetime.fromisoformat(row["data_hora_gmt"].replace("Z", "+00:00"))
        if detected.tzinfo is None:
            detected = detected.replace(tzinfo=UTC)
        if not end - timedelta(hours=hours) <= detected <= end:
            raise ValueError("Detecção fora do período")
        state_code = row.get("id_1") or code[:2]
        counts[code] += 1
        counts[state_code] += 1
        latest[state_code] = max(latest.get(state_code, detected), detected)
        if detected >= end - timedelta(hours=24):
            recent[code] += 1
            recent[state_code] += 1
        latest[code] = max(latest.get(code, detected), detected)
    municipalities = []
    for area in areas + (states or []):
        code = area["ibge_code"]
        km2 = area["area_km2"]
        valid_area = km2 is not None and math.isfinite(km2) and km2 > 0
        municipalities.append(
            FireMunicipality(
                **{**area, "area_km2": round(km2, 3) if valid_area else None},
                count=counts[code],
                count_24h=recent[code],
                density=round(counts[code] * 1000 / km2, 3) if valid_area else None,
                latest_detection_at=latest.get(code),
            )
        )
    return FireSummary(
        window_start=end - timedelta(hours=hours),
        window_end=end,
        hours=hours,
        total=len(rows),
        municipalities=[item for item in municipalities if len(item.ibge_code) == 7],
        states=[item for item in municipalities if len(item.ibge_code) == 2],
        unassigned_count=len(rows)
        - sum(item.count for item in municipalities if len(item.ibge_code) == 7),
    )


async def get_summary(
    session: AsyncSession, *, level: FireScope, parent: str | None, hours: int, at: datetime
) -> FireSummary:
    if at.tzinfo is None or not -timedelta(minutes=1) <= datetime.now(UTC) - at <= timedelta(
        hours=2
    ):
        raise InvalidParameterError("Atualize a camada antes de consultar o resumo.", "at")
    scope = await _scope_filter(session, level, parent)
    key = f"{scope}:{hours}:{at.isoformat()}"
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached
        cached = await redis_cache.read("fire-summary", key, FireSummary)
        if cached is not None:
            _cache.set(key, cached, ttl_seconds=30)
            return cached
        if _failures.get(key):
            raise ProviderError("Resumo INPE temporariamente indisponível.")
        try:
            cql = _time_filter(scope, at, hours)
            _, total = await _fetch_wfs(cql, 1)
            rows = await _fetch_rows(cql, total)
            _, verified_total = await _fetch_wfs(cql, 1)
            if verified_total != total:
                raise ValueError("A fonte recebeu novas detecções durante a paginação")
            areas = await municipality_areas(session)
            if parent:
                areas = [area for area in areas if area["ibge_code"].startswith(parent)]
            states = await state_areas(session) if level != "municipality" else []
            if parent:
                states = [area for area in states if area["ibge_code"] == parent[:2]]
            result = aggregate(rows, areas, at, hours, states)
            _cache.set(key, result)
            await redis_cache.write("fire-summary", key, result, 7200)
            return result
        except (httpx.HTTPError, ValueError, KeyError, TypeError, ProviderError) as exc:
            _failures.set(key, True)
            raise ProviderError(
                "Não foi possível obter o resumo completo dos focos no INPE."
            ) from exc
