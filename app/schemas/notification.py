from __future__ import annotations

import base64
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import Field, ValidationInfo, field_validator

from app.schemas.common import CamelModel


class PushEndpoint(CamelModel):
    endpoint: str = Field(max_length=2000)

    @field_validator("endpoint")
    @classmethod
    def official_push_service(cls, value: str) -> str:
        url = urlsplit(value)
        host = (url.hostname or "").lower()
        domains = (
            "push.services.mozilla.com",
            "push.apple.com",
            "notify.windows.com",
            "wns.windows.com",
        )
        allowed = host == "fcm.googleapis.com" or any(
            host == domain or host.endswith("." + domain) for domain in domains
        )
        if not (
            allowed
            and url.scheme == "https"
            and url.port in (None, 443)
            and not url.username
            and not url.password
            and not url.fragment
        ):
            raise ValueError("Use uma inscrição HTTPS de um serviço de push reconhecido.")
        return value


class PushKeys(CamelModel):
    p256dh: str = Field(max_length=100)
    auth: str = Field(max_length=30)

    @field_validator("p256dh", "auth")
    @classmethod
    def valid_key(cls, value: str, info: ValidationInfo) -> str:
        # A chave pública precisa ser um ponto P-256 real; auth tem 16 bytes.
        try:
            raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
            if info.field_name == "p256dh":
                ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
            elif len(raw) != 16:
                raise ValueError("tamanho inválido")
        except ValueError as exc:
            raise ValueError("A chave de inscrição de notificações é inválida.") from exc
        return value


class PushRegistration(PushEndpoint):
    keys: PushKeys


class PushConfig(CamelModel):
    public_key: str | None
