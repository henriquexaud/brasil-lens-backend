from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from app.core.password_policy import is_common_password
from app.schemas.common import CamelModel

Theme = Literal["light", "dark"]


class LoginRequest(CamelModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class RegisterRequest(LoginRequest):
    name: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=128)
    theme: Theme = "light"
    # O cadastro só existe com o aceite da Política de Privacidade e dos Termos de Uso.
    accept_terms: Literal[True]

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def reject_common_password(cls, value: str) -> str:
        if is_common_password(value):
            raise ValueError(
                "Esta senha é muito comum. Escolha uma senha mais difícil de adivinhar."
            )
        return value


class UserOut(CamelModel):
    id: str
    name: str
    email: str
    theme: Theme
    # Falso quando a conta ainda não aceitou a versão vigente dos documentos.
    terms_accepted: bool


class ThemeUpdate(CamelModel):
    theme: Theme


class AccountDeletion(CamelModel):
    password: str = Field(min_length=1, max_length=128)


class AccountRecord(CamelModel):
    id: str
    name: str
    email: str
    theme: Theme
    created_at: datetime
    terms_version: str | None
    terms_accepted_at: datetime | None


class FollowedRecord(CamelModel):
    municipality_code: str
    name: str | None
    state_abbreviation: str | None
    followed_at: datetime
    notifications_enabled: bool
    notifications_opt_in_at: datetime | None


class DeviceRecord(CamelModel):
    # Só o serviço de push do navegador: o endpoint completo e as chaves permitem enviar
    # mensagens ao aparelho e não saem do banco.
    push_service: str
    registered_at: datetime


class NotificationRecord(CamelModel):
    created_at: datetime
    municipality_code: str
    content: dict[str, Any]


class AccountExport(CamelModel):
    exported_at: datetime
    account: AccountRecord
    followed_municipalities: list[FollowedRecord]
    devices: list[DeviceRecord]
    notifications: list[NotificationRecord]
    active_sessions: int
