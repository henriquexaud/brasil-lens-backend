"""Atualização periódica das fontes climáticas — laço `asyncio` em processo.

`docs/ARCHITECTURE.md` recusou fila/agendador/Redis para o MVP por falta de
necessidade concreta (§1.4, §2). Agora há uma: estações do INMET atualizam
por hora e avisos podem mudar a qualquer momento — rodar o job só quando
alguém lembra de invocar a CLI não é "tempo real". A solução ainda não pede
infraestrutura nova: um laço por fonte, iniciado no `lifespan` de
`app/main.py` (que já existe e já gerencia startup/shutdown), chamando a
mesma função de job que a CLI usa.

Uma falha de rede numa fonte não derruba o laço nem a API — fica registrada
em `ingestion_runs` (pelo próprio `job_session`) e visível em
`GET /weather/sources`. `start()`/`stop()` são as duas únicas funções que
`app/main.py` precisa conhecer.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from app.core.config import settings
from app.core.logging import get_logger
from app.jobs import import_weather_cemaden_alerts, import_weather_inmet_alerts

logger = get_logger(__name__)

# `main()` de cada job — a mesma função que `python -m app.jobs.x` chama via
# `run_job`, só que aqui invocada direto (sem o wrapper que faz
# `asyncio.run`/`SystemExit`/`dispose_engine`, que fecharia a engine da API).
# Condições atuais vêm da Open-Meteo sob demanda e com cache.
# Os jobs legados de estações continuam disponíveis pela CLI. INMET e CEMADEN
# rodam no mesmo intervalo por simplicidade — nada hoje pede cadências
# diferentes; se algum dia pedir, é só passar `interval` por job aqui.
_JOBS: tuple[tuple[str, Callable[[], Awaitable[int]]], ...] = (
    ("inmet_alerts", import_weather_inmet_alerts.main),
    ("cemaden_alerts", import_weather_cemaden_alerts.main),
)

_tasks: list[asyncio.Task[None]] = []


async def _loop(name: str, job: Callable[[], Awaitable[int]]) -> None:
    while True:
        try:
            await job()
        except Exception:
            # `job_session` já registra a falha em `ingestion_runs`; o
            # catch aqui é só para o laço sobreviver a um bug inesperado no
            # próprio job, não a uma falha de rede (essa já vira `ProviderError`
            # tratado dentro de cada `main()`).
            logger.exception("weather_scheduler.cycle_failed", extra={"job": name})
        await asyncio.sleep(settings.weather_refresh_interval_seconds)


def start() -> None:
    """Inicia um laço por fonte. Sem efeito se já estiver rodando ou desligado."""
    if not settings.weather_refresh_enabled or _tasks:
        return
    for name, job in _JOBS:
        _tasks.append(asyncio.create_task(_loop(name, job), name=f"weather-refresh-{name}"))
    logger.info("weather_scheduler.started", extra={"jobs": [name for name, _ in _JOBS]})


async def stop() -> None:
    """Cancela os laços e espera o cancelamento propagar, para shutdown limpo."""
    for task in _tasks:
        task.cancel()
    for task in _tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    _tasks.clear()
