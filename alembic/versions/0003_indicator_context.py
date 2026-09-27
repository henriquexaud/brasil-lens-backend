from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_indicator_context"
down_revision: str | None = "0002_saved_views"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATA_CONTEXT_VALUES: tuple[str, ...] = (
    "sociopolitical",
    "climate_environmental",
    "biodiversity",
)

DATA_CONTEXT = postgresql.ENUM(*DATA_CONTEXT_VALUES, name="data_context", create_type=False)


def upgrade() -> None:
    rendered = ", ".join(f"'{value}'" for value in DATA_CONTEXT_VALUES)
    op.execute(f"CREATE TYPE data_context AS ENUM ({rendered})")

    op.add_column(
        "indicators",
        sa.Column(
            "context",
            DATA_CONTEXT,
            nullable=False,
            server_default="sociopolitical",
        ),
    )
    op.create_index("ix_indicators_context", "indicators", ["context"])


def downgrade() -> None:
    op.drop_index("ix_indicators_context", table_name="indicators")
    op.drop_column("indicators", "context")
    op.execute("DROP TYPE IF EXISTS data_context")
