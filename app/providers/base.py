"""Infraestrutura mínima compartilhada pelos providers externos.

Um provider só faz duas coisas: falar HTTP com a fonte e devolver **tipos
internos**. Nada abaixo desta pasta conhece SQLAlchemy, e nada acima dela
conhece o formato de resposta do IBGE.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Falhas transitórias da fonte — queda de conexão, timeout, 429 e 5xx — são
# rotina nas APIs do IBGE sob carga. Medido numa ingestão real: 2 das 27 UFs
# caíram com "Server disconnected without sending a response" e responderam
# normalmente na repetição. Sem nova tentativa, uma única queda derruba o
# escopo inteiro (todos os municípios da UF). Erros definitivos — 404, corpo
# que não é JSON — não se repetem: tentar de novo só atrasaria a mesma falha.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


@asynccontextmanager
async def http_client(
    base_url: str | None = None,
    *,
    timeout: float | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """Cliente HTTP com defaults do IBGE, sobrepostos por qualquer provider.

    `base_url`/`timeout` ausentes preservam o comportamento de hoje (todo
    provider fala com o IBGE). Um provider novo, de outra fonte, passa os dois
    explicitamente — sem isso, ele herdaria silenciosamente o timeout
    calibrado para as malhas municipais do IBGE, que não tem relação com a
    latência de nenhuma outra API.
    """
    async with httpx.AsyncClient(
        base_url=base_url or settings.ibge_base_url,
        timeout=timeout if timeout is not None else settings.ibge_http_timeout,
        follow_redirects=True,
        headers={"Accept-Encoding": "gzip", "User-Agent": "brasil-lens/0.1 (ingestion)"},
    ) as client:
        yield client


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    source: str,
) -> Any:
    """GET + parse de JSON, convertendo qualquer falha em `ProviderError`.

    Erros de fonte externa precisam ser distinguíveis de bugs nossos: a ingestão
    trata `ProviderError` como escopo falho e segue com os demais.
    """
    response = await _get_with_retry(client, url, params=params, source=source)

    if response.status_code != httpx.codes.OK:
        raise ProviderError(
            f"{source} respondeu HTTP {response.status_code}.",
            url=str(response.request.url),
            status=response.status_code,
            body=response.text[:500],
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(
            f"{source} respondeu conteúdo que não é JSON.",
            url=str(response.request.url),
            body=response.text[:500],
        ) from exc


async def _get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None,
    source: str,
) -> httpx.Response:
    """GET com novas tentativas apenas para falhas transitórias.

    A espera cresce a cada tentativa (2 s, 4 s): dá à fonte tempo de se
    recuperar sem transformar uma falha real em minutos de espera.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.get(url, params=params)
        except httpx.TransportError as exc:
            if attempt == MAX_ATTEMPTS:
                raise ProviderError(
                    f"Falha de rede ao consultar {source}: {exc}", url=url, attempts=attempt
                ) from exc
            reason = str(exc) or type(exc).__name__
        except httpx.HTTPError as exc:
            raise ProviderError(f"Falha de rede ao consultar {source}: {exc}", url=url) from exc
        else:
            if response.status_code not in _TRANSIENT_STATUS or attempt == MAX_ATTEMPTS:
                return response
            reason = f"HTTP {response.status_code}"

        delay = RETRY_BACKOFF_SECONDS * attempt
        logger.warning(
            "provider.retry",
            extra={
                "source": source,
                "url": url,
                "attempt": attempt,
                "reason": reason,
                "delay_seconds": delay,
            },
        )
        await asyncio.sleep(delay)

    raise AssertionError("inalcançável: o laço sempre retorna ou levanta")  # pragma: no cover
