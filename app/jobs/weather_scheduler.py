from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from app.core.config import settings
from app.core.logging import get_logger
from app.jobs import (
    dispatch_notifications,
    import_weather_cemaden_alerts,
    import_weather_inmet_alerts,
)

logger = get_logger(__name__)

_JOBS: tuple[tuple[str, Callable[[], Awaitable[int]]], ...] = (
    ("inmet_alerts", import_weather_inmet_alerts.main),
    ("cemaden_alerts", import_weather_cemaden_alerts.main),
    ("notifications", dispatch_notifications.main),
)

_tasks: list[asyncio.Task[None]] = []


async def _loop(name: str, job: Callable[[], Awaitable[int]]) -> None:
    while True:
        try:
            await job()
        except Exception:
            logger.exception("weather_scheduler.cycle_failed", extra={"job": name})
        await asyncio.sleep(settings.weather_refresh_interval_seconds)


def start() -> None:
    if not settings.weather_refresh_enabled or _tasks:
        return
    for name, job in _JOBS:
        _tasks.append(asyncio.create_task(_loop(name, job), name=f"weather-refresh-{name}"))
    logger.info("weather_scheduler.started", extra={"jobs": [name for name, _ in _JOBS]})


async def stop() -> None:
    for task in _tasks:
        task.cancel()
    for task in _tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    _tasks.clear()
