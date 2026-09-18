"""Contratos das visualizações salvas.

O contrato usa `year` como **string** ("latest" ou "2022"), exatamente como a
rota `/map` — assim o cliente guarda e reenvia o mesmo valor que já usa no
seletor de ano, sem conversão. A tradução para a coluna `reference_year`
(NULL quando "latest") acontece no serviço.
"""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.models import TerritoryLevel
from app.schemas.common import CamelModel, Pagination

LATEST_YEAR = "latest"

_YEAR_PATTERN = re.compile(r"^\d{4}$")


def normalize_year(value: str | int | None) -> str:
    """Aceita "latest", "2022" ou 2022 e devolve sempre a forma canônica em texto."""
    if value is None:
        return LATEST_YEAR
    text = str(value).strip().lower()
    if text in ("", LATEST_YEAR):
        return LATEST_YEAR
    if not _YEAR_PATTERN.match(text) or not 1900 <= int(text) <= 2100:
        raise ValueError("O campo 'year' aceita um ano entre 1900 e 2100 ou 'latest'.")
    return text


# A tradução entre o contrato e a coluna mora aqui, ao lado de `normalize_year`:
# são as duas metades da mesma regra, e separá-las é como um par
# serializar/desserializar acaba divergindo.


def year_to_column(year: str) -> int | None:
    """Contrato → coluna: "latest" vira NULL; um ano vira o próprio inteiro."""
    return None if year == LATEST_YEAR else int(year)


def column_to_year(reference_year: int | None) -> str:
    """Coluna → contrato: NULL vira "latest"."""
    return LATEST_YEAR if reference_year is None else str(reference_year)


class SavedViewBase(CamelModel):
    """Campos que descrevem um recorte do mapa — os mesmos da rota `/map`."""

    name: str = Field(min_length=1, max_length=80, description="Rótulo escolhido pelo usuário.")
    description: str | None = Field(default=None, max_length=280)
    level: TerritoryLevel = Field(description="Nível territorial desenhado no mapa.")
    parent_code: str | None = Field(
        default=None,
        max_length=9,
        description="Código IBGE do pai. Obrigatório quando level='municipality'.",
    )
    indicator_key: str = Field(min_length=1, max_length=64)
    year: str = Field(default=LATEST_YEAR, description="Ano de referência ou 'latest'.")
    classes: int = Field(default=5, ge=2, le=9)

    @field_validator("name", "indicator_key", mode="before")
    @classmethod
    def _strip_required(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", "parent_code", mode="before")
    @classmethod
    def _strip_optional(cls, value: object) -> object:
        # Campo em branco vindo de um formulário é ausência, não string vazia.
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("year", mode="before")
    @classmethod
    def _year(cls, value: object) -> str:
        return normalize_year(value)  # type: ignore[arg-type]


class SavedViewCreate(SavedViewBase):
    """Corpo do POST /views."""


class SavedViewUpdate(SavedViewBase):
    """Corpo do PUT /views/{id}.

    PUT é substituição completa, então o corpo tem a mesma forma do POST: o
    cliente reenvia a visualização inteira. Um PATCH parcial só faria sentido
    se a entidade fosse grande — esta tem seis campos.
    """


class SavedViewOut(CamelModel):
    """Como a visualização volta para o cliente.

    `id` é o UUID público: o id serial do banco não aparece no contrato, como
    em todo o resto da API.
    """

    id: UUID
    name: str
    description: str | None = None
    level: TerritoryLevel
    parent_code: str | None = None
    indicator_key: str
    year: str
    classes: int
    created_at: datetime
    updated_at: datetime


class SavedViewListResponse(CamelModel):
    views: list[SavedViewOut]
    pagination: Pagination
