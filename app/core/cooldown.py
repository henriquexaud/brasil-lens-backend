from __future__ import annotations

import math
import time

from app.core.logging import get_logger

logger = get_logger(__name__)
_registry: list[SourceCooldown] = []


class SourceCooldown:
    def __init__(self, source: str, seconds: float) -> None:
        self.source = source
        self.seconds = seconds
        self._until = 0.0
        _registry.append(self)

    @property
    def active(self) -> bool:
        return time.monotonic() < self._until

    def remaining_seconds(self) -> int:
        return max(0, math.ceil(self._until - time.monotonic()))

    def trip(self) -> None:
        if not self.active:
            logger.warning(
                "provider.cooldown_started", extra={"source": self.source, "seconds": self.seconds}
            )
        self._until = time.monotonic() + self.seconds

    def reset(self) -> None:
        self._until = 0.0


def reset_all() -> None:
    for cooldown in _registry:
        cooldown.reset()
