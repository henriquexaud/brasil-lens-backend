"""Contratos dos municípios seguidos.

O banco guarda só `(usuário, código IBGE)`. Nome e UF saem por JOIN na
leitura, para a lista do cliente não precisar de um request por município.
"""

from __future__ import annotations

from datetime import datetime

from app.schemas.common import CamelModel


class FollowedMunicipalityOut(CamelModel):
    municipality_code: str
    # Nulos só se o território deixar de existir no catálogo depois de seguido:
    # o vínculo sobrevive a uma reingestão, e o cliente cai no código.
    name: str | None = None
    state_code: str | None = None
    state_name: str | None = None
    state_abbreviation: str | None = None
    followed_at: datetime


class FollowedMunicipalityListResponse(CamelModel):
    """Lista completa, sem paginação: são os municípios de *um* usuário."""

    municipalities: list[FollowedMunicipalityOut]
