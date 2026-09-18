"""Erros de domínio e o envelope único de erro da API.

Todo erro sai como ``{"error": {"code", "message", "details"}}`` — inclusive os
422 de validação do FastAPI, para o frontend ter um único formato a tratar.
"""

from typing import Any


class DomainError(Exception):
    """Base dos erros previsíveis do domínio, cada um com seu status HTTP."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = {k: v for k, v in details.items() if v is not None}


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class TerritoryNotFoundError(NotFoundError):
    code = "territory_not_found"

    def __init__(self, ibge_code: str) -> None:
        super().__init__(
            f"Nenhum território encontrado para o código IBGE '{ibge_code}'.",
            ibgeCode=ibge_code,
        )


class IndicatorNotFoundError(NotFoundError):
    code = "indicator_not_found"

    def __init__(self, key: str) -> None:
        super().__init__(f"Indicador '{key}' não existe no catálogo.", indicator=key)


class SavedViewNotFoundError(NotFoundError):
    code = "saved_view_not_found"

    def __init__(self, view_id: str) -> None:
        super().__init__(f"Visualização '{view_id}' não existe.", viewId=view_id)


class ConflictError(DomainError):
    """Estado atual do recurso impede a escrita (ex.: nome já usado)."""

    status_code = 409
    code = "conflict"


class SavedViewNameTakenError(ConflictError):
    code = "saved_view_name_taken"

    def __init__(self, name: str) -> None:
        super().__init__(f"Já existe uma visualização chamada '{name}'.", name=name)


class InvalidParameterError(DomainError):
    status_code = 400
    code = "invalid_parameter"

    def __init__(self, message: str, parameter: str | None = None, **details: Any) -> None:
        super().__init__(message, parameter=parameter, **details)


class ProviderError(DomainError):
    """Falha ao consultar ou interpretar uma fonte externa.

    Só ocorre durante a ingestão: nenhuma requisição de usuário toca fonte externa.
    """

    status_code = 502
    code = "provider_error"


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body
