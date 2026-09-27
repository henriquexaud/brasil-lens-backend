from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    FollowedMunicipalityNotFoundError,
    InvalidParameterError,
    TerritoryNotFoundError,
)
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
        notifications_enabled=row.notifications_enabled,
    )


async def list_followed(session: AsyncSession, user_id: str) -> FollowedMunicipalityListResponse:
    rows = await followed_repo.list_for_user(session, user_id)
    return FollowedMunicipalityListResponse(municipalities=[_to_schema(row) for row in rows])


async def follow(
    session: AsyncSession, user_id: str, code: str
) -> tuple[FollowedMunicipalityOut, bool]:
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
    assert row is not None
    return _to_schema(row), created


async def unfollow(session: AsyncSession, user_id: str, code: str) -> None:
    await followed_repo.unfollow(session, user_id, code)


async def set_notifications(
    session: AsyncSession, user_id: str, code: str, enabled: bool
) -> FollowedMunicipalityOut:
    found = await followed_repo.set_notifications(session, user_id, code, enabled)
    if not found:
        raise FollowedMunicipalityNotFoundError(code)
    row = await followed_repo.get_for_user(session, user_id, code)
    assert row is not None
    return _to_schema(row)
