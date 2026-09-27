from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_weather_alert_description"
down_revision: str | None = "0005_territory_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("weather_alerts", sa.Column("description", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("weather_alerts", "description")
