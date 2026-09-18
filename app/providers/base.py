"""Infraestrutura mínima compartilhada pelos providers externos.

Um provider só faz duas coisas: falar HTTP com a fonte e devolver **tipos
internos**. Nada abaixo desta pasta conhece SQLAlchemy, e nada acima dela
conhece o formato de resposta do IBGE.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.logging import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def http_client(base_url: str | None = None) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=base_url or settings.ibge_base_url,
        timeout=settings.ibge_http_timeout,
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
    try:
        response = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Falha de rede ao consultar {source}: {exc}", url=url) from exc

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
