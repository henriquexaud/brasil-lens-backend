"""Caso de uso dos municípios seguidos.

Seguir e deixar de seguir são **idempotentes**: o cliente aplica a mudança de
forma otimista e pode reenviar o mesmo pedido (duplo clique, nova tentativa)
sem transformar isso em erro. O único erro de escrita é tentar seguir algo que
não é um município do catálogo.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParameterError, TerritoryNotFoundError
from app.models import TerritoryLevel
from app.repositories import followed_municipalities as followed_repo
from app.repositories import territories as territories_repo
from app.repositories.followed_municipalities import FollowedRow
from app.schemas.followed_municipality import (
    FollowedMunicipalityListResponse,
    FollowedMunicipalityOut,
)


def _to_schema(row: FollowedRow) -> FollowedMunicipalityOut:
    return FollowedMunicipalityOut(
        municipality_code=row.municipality_code,
        name=row.name,
        state_code=row.state_code,
        state_name=row.state_name,
        state_abbreviation=row.state_abbreviation,
        followed_at=row.followed_at,
    )


async def list_followed(session: AsyncSession, user_id: str) -> FollowedMunicipalityListResponse:
    rows = await followed_repo.list_for_user(session, user_id)
    return FollowedMunicipalityListResponse(municipalities=[_to_schema(row) for row in rows])


async def follow(
    session: AsyncSession, user_id: str, code: str
) -> tuple[FollowedMunicipalityOut, bool]:
    """Segue o município. Devolve o vínculo e se ele acabou de ser criado."""
    level = await territories_repo.get_level_by_code(session, code)
    if level is None:
        raise TerritoryNotFoundError(code)
    if level is not TerritoryLevel.MUNICIPALITY:
        raise InvalidParameterError(
            f"Só municípios podem ser seguidos, mas '{code}' é '{level.value}'.",
            parameter="municipalityCode",
        )

    created = await followed_repo.follow(session, user_id, code)
    row = await followed_repo.get_for_user(session, user_id, code)
    assert row is not None  # acabou de ser gravado (ou já existia) nesta transação
    return _to_schema(row), created


async def unfollow(session: AsyncSession, user_id: str, code: str) -> None:
    # Deixar de seguir o que não se segue já é o estado pedido: 204, não 404.
    # Diferente de apagar uma visualização por id, aqui o recurso é endereçado
    # pelo município, e o cliente pode repetir o pedido depois de um otimista.
    await followed_repo.unfollow(session, user_id, code)
