from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_user_accounts"
down_revision: str | None = "0009_remove_socioeconomic"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("email", sa.String(254), unique=True),
        sa.Column("password_hash", sa.String(256)),
        sa.Column("theme", sa.String(5), nullable=False, server_default="light"),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("theme IN ('light', 'dark')", name="theme_valid"),
        sa.CheckConstraint(
            "(email IS NULL) = (password_hash IS NULL)", name="credentials_together"
        ),
    )
    op.execute(
        "INSERT INTO users (id, name) "
        "SELECT DISTINCT user_id, 'Conta legada' FROM followed_municipalities"
    )
    op.create_foreign_key(
        "fk_followed_municipalities_user_id_users",
        "followed_municipalities",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_table(
        "user_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("user_sessions")
    op.drop_constraint(
        "fk_followed_municipalities_user_id_users", "followed_municipalities", type_="foreignkey"
    )
    op.drop_table("users")
