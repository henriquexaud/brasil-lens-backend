from __future__ import annotations

from datetime import datetime

from app.schemas.common import CamelModel


class FollowedMunicipalityOut(CamelModel):
    municipality_code: str
    name: str | None = None
    state_code: str | None = None
    state_name: str | None = None
    state_abbreviation: str | None = None
    followed_at: datetime
    notifications_enabled: bool


class FollowedMunicipalityListResponse(CamelModel):
    municipalities: list[FollowedMunicipalityOut]


class NotificationsUpdate(CamelModel):
    enabled: bool
