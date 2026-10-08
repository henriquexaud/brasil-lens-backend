from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_political"
down_revision: str | None = "0012_socioeconomic"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "political_releases",
        sa.Column("year", sa.SmallInteger(), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("ingestion_runs.id"), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "political_candidates",
        sa.Column(
            "year", sa.SmallInteger(), sa.ForeignKey("political_releases.year"), primary_key=True
        ),
        sa.Column("candidate_id", sa.String(24), primary_key=True),
        sa.Column("office", sa.String(24), nullable=False),
        sa.Column("scope_code", sa.String(9), nullable=False),
        sa.Column("elected_round", sa.SmallInteger()),
        sa.Column("data", postgresql.JSONB(), nullable=False),
    )
    op.create_index(
        "ix_political_candidates_scope", "political_candidates", ["year", "office", "scope_code"]
    )
    op.create_table(
        "political_results",
        sa.Column(
            "year", sa.SmallInteger(), sa.ForeignKey("political_releases.year"), primary_key=True
        ),
        sa.Column("office", sa.String(24), primary_key=True),
        sa.Column("round", sa.SmallInteger(), primary_key=True),
        sa.Column(
            "territory_code", sa.String(9), sa.ForeignKey("territories.ibge_code"), primary_key=True
        ),
        sa.Column("data", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_political_results_scope", "political_results", ["year", "office", "round"])


def downgrade() -> None:
    op.drop_table("political_results")
    op.drop_table("political_candidates")
    op.drop_table("political_releases")
