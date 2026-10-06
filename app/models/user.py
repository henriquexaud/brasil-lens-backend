from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # Registros anteriores à autenticação preservam seus vínculos, sem credenciais.
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(256))
    theme: Mapped[str] = mapped_column(String(5), nullable=False, server_default="light")

    __table_args__ = (
        CheckConstraint("theme IN ('light', 'dark')", name="theme_valid"),
        CheckConstraint("(email IS NULL) = (password_hash IS NULL)", name="credentials_together"),
    )


class UserSession(Base):
    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, index=True
    )
