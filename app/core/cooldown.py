"""Pausa das consultas a uma fonte externa depois de uma falha.

Uma fonte fora do ar não deve receber uma nova tentativa a cada requisição do
mapa: cada uma esperaria o timeout inteiro e somaria carga a um serviço que já
não responde. Durante a pausa quem consulta recebe a falha (ou o fallback) na
hora; terminada a pausa, a próxima consulta real serve de sonda.

A pausa vale para a fonte inteira, não para um recorte: se o INPE caiu, ele
caiu para todas as UFs.
"""

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
        """Inicia a pausa após uma falha da fonte."""
        if not self.active:
            logger.warning(
                "provider.cooldown_started", extra={"source": self.source, "seconds": self.seconds}
            )
        self._until = time.monotonic() + self.seconds

    def reset(self) -> None:
        self._until = 0.0


def reset_all() -> None:
    """Encerra todas as pausas — usado pelos testes para não vazar estado."""
    for cooldown in _registry:
        cooldown.reset()
