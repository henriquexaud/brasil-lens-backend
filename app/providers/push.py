from __future__ import annotations

import asyncio
from typing import Any

import orjson
from pywebpush import WebPushException, webpush
from requests.exceptions import RequestException

from app.core.config import settings
from app.core.errors import ProviderError


def _send(subscription: dict[str, Any], payload: dict[str, Any], ttl: int, topic: str) -> None:
    assert settings.vapid_private_key is not None
    try:
        webpush(
            subscription_info=subscription,
            data=orjson.dumps(payload).decode(),
            vapid_private_key=settings.vapid_private_key.get_secret_value(),
            vapid_claims={"sub": settings.vapid_subject},
            ttl=ttl,
            timeout=10,
            headers={"Topic": topic, "Urgency": "normal"},
        )
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        raise ProviderError("O serviço de notificações recusou o envio.", status=status) from exc
    except RequestException as exc:
        raise ProviderError("Falha de conexão com o serviço de notificações.") from exc
    except (ValueError, TypeError) as exc:
        raise ProviderError(
            "A configuração de notificações é inválida.", configuration=True
        ) from exc


async def send(subscription: dict[str, Any], payload: dict[str, Any], ttl: int, topic: str) -> None:
    await asyncio.to_thread(_send, subscription, payload, ttl, topic)
