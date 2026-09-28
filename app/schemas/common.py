from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel

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


def to_json(model: BaseModel) -> bytes:
    """Corpo da resposta como a rota o serializaria, pronto para cache e reenvio.

    Um GeoJSON guardado como modelo ocupa ~9x mais memória que o JSON e é revalidado e
    serializado de novo a cada resposta.
    """
    return model.model_dump_json(by_alias=True).encode()


class ErrorDetail(CamelModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(CamelModel):
    error: ErrorDetail


class Pagination(CamelModel):
    total: int
    limit: int
    offset: int
