from typing import Any


class DomainError(Exception):
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


class FollowedMunicipalityNotFoundError(NotFoundError):
    code = "followed_municipality_not_found"

    def __init__(self, municipality_code: str) -> None:
        super().__init__(
            f"Você não segue o município '{municipality_code}'.",
            municipalityCode=municipality_code,
        )


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class AuthenticationError(DomainError):
    status_code = 401
    code = "authentication_required"


class ForbiddenError(DomainError):
    status_code = 403
    code = "forbidden"


class AuthRateLimitedError(DomainError):
    status_code = 429
    code = "auth_rate_limited"


class InvalidParameterError(DomainError):
    status_code = 400
    code = "invalid_parameter"

    def __init__(self, message: str, parameter: str | None = None, **details: Any) -> None:
        super().__init__(message, parameter=parameter, **details)


class ProviderError(DomainError):
    status_code = 502
    code = "provider_error"


class ProviderRateLimitedError(ProviderError):
    status_code = 503
    code = "provider_rate_limited"

    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message, retryAfterSeconds=retry_after_seconds)
        self.retry_after_seconds = retry_after_seconds


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body
