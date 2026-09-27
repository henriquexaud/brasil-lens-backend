from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class FollowedMunicipality(Base, TimestampMixin):
    __tablename__ = "followed_municipalities"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)

    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    municipality_code: Mapped[str] = mapped_column(String(7), nullable=False)
    notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "municipality_code"),
        CheckConstraint("municipality_code ~ '^[0-9]{7}$'", name="municipality_code_format"),
        CheckConstraint("length(btrim(user_id)) > 0", name="user_id_not_blank"),
        Index("ix_followed_municipalities_municipality_code", "municipality_code"),
    )

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico
        return f"<FollowedMunicipality {self.user_id} → {self.municipality_code}>"
