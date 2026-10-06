from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionFactory
from app.jobs._runner import run_job
from app.services.notifications import dispatch

logger = get_logger(__name__)


async def main() -> int:
    if not settings.push_notifications_enabled:
        return 0
    if not settings.vapid_private_key or not settings.vapid_public_key:
        logger.warning("notifications.missing_configuration")
        return 1
    async with SessionFactory() as session:
        sent = await dispatch(session)
    logger.info("notifications.cycle_completed", extra={"accepted": sent})
    return 0


if __name__ == "__main__":
    run_job(main)
