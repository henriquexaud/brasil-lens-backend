"""Healthchecks.

`/health` responde sem tocar o banco (liveness). `/health/ready` verifica banco
e PostGIS (readiness) — é o que um orquestrador deve usar para decidir tráfego.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import settings
from app.core.logging import get_logger
from app.services.map import cache_stats

router = APIRouter(tags=["health"])
logger = get_logger(__name__)


@router.get("/health", summary="Liveness")
async def health() -> dict[str, Any]:
    return {"status": "ok", "environment": settings.app_env}


@router.get("/health/ready", summary="Readiness (banco + PostGIS)")
async def ready(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
        postgis_version = (await session.execute(text("SELECT PostGIS_Lib_Version()"))).scalar_one()
        checks["postgis"] = postgis_version
    except Exception as exc:
        logger.warning("health.not_ready", extra={"error": str(exc)})
        checks["database"] = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "checks": checks}

    return {"status": "ok", "checks": checks, "readCache": cache_stats()}
