from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.core.errors import ProviderError
from app.providers import base

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "RETRY_BACKOFF_SECONDS", 0)


def _scripted(*steps: httpx.Response | Exception) -> tuple[Handler, list[int]]:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        step = steps[len(calls)]
        calls.append(1)
        if isinstance(step, Exception):
            raise step
        return step

    return handler, calls


def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://ibge.test")


async def test_queda_de_conexao_seguida_de_sucesso_devolve_o_payload() -> None:
    handler, calls = _scripted(
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.Response(200, json={"ok": True}),
    )
    async with _client(handler) as client:
        assert await base.get_json(client, "/x", source="teste") == {"ok": True}
    assert len(calls) == 2


async def test_5xx_transitorio_e_repetido() -> None:
    handler, calls = _scripted(httpx.Response(503), httpx.Response(200, json=[1, 2]))
    async with _client(handler) as client:
        assert await base.get_json(client, "/x", source="teste") == [1, 2]
    assert len(calls) == 2


async def test_falha_persistente_desiste_depois_do_limite() -> None:
    handler, calls = _scripted(*[httpx.ConnectError("recusada")] * base.MAX_ATTEMPTS)
    async with _client(handler) as client:
        with pytest.raises(ProviderError, match="Falha de rede"):
            await base.get_json(client, "/x", source="teste")
    assert len(calls) == base.MAX_ATTEMPTS


async def test_404_falha_na_hora_sem_repetir() -> None:
    handler, calls = _scripted(httpx.Response(404, text="não existe"))
    async with _client(handler) as client:
        with pytest.raises(ProviderError, match="HTTP 404"):
            await base.get_json(client, "/x", source="teste")
    assert len(calls) == 1
