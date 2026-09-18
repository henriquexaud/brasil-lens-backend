"""Base dos contratos da API.

Todos os campos saem em camelCase para o frontend, mas continuam snake_case no
Python. `populate_by_name` permite construir os modelos pelos nomes internos.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel

# Decimal é o tipo correto no Python (aritmética exata em estatísticas e
# valores monetários), mas o padrão do Pydantic é serializá-lo como *string*
# JSON. Isso obrigaria o frontend a dar parseFloat em cada valor — exatamente o
# tipo de trabalho que a API deve absorver. JavaScript só tem double, então
# emitir número JSON é o contrato honesto.
ApiDecimal = Annotated[
    Decimal,
    PlainSerializer(float, return_type=float, when_used="json"),
]


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        ser_json_inf_nan="null",
    )


class ErrorDetail(CamelModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(CamelModel):
    """Envelope único de erro — inclusive para os 422 de validação."""

    error: ErrorDetail


class Pagination(CamelModel):
    total: int
    limit: int
    offset: int
