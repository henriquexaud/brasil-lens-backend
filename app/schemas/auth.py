from typing import Literal

from pydantic import Field, field_validator

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

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class UserOut(CamelModel):
    id: str
    name: str
    email: str
    theme: Theme


class ThemeUpdate(CamelModel):
    theme: Theme
