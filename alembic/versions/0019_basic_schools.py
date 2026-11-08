from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_basic_schools"
down_revision: str | None = "0018_user_consent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Lista de escolas públicas do município na aba Básica (ADR-23): só o Censo
    # Escolar mais recente, regravado inteiro a cada ingestão.
    op.create_table(
        "basic_schools",
        sa.Column("code", sa.Integer(), primary_key=True),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column(
            "territory_id",
            sa.Integer(),
            sa.ForeignKey("territories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("network", sa.String(16), nullable=False),
        sa.Column("enrollment", sa.Integer(), nullable=False),
        sa.Column("early_childhood_enrollment", sa.Integer(), nullable=False),
        sa.Column("elementary_enrollment", sa.Integer(), nullable=False),
        sa.Column("high_school_enrollment", sa.Integer(), nullable=False),
        sa.Column(
            "dataset_id",
            sa.SmallInteger(),
            sa.ForeignKey("datasets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "network IN ('federal', 'state', 'municipal')",
            name=op.f("ck_basic_schools_network"),
        ),
    )
    op.create_index(op.f("ix_basic_schools_territory_id"), "basic_schools", ["territory_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_basic_schools_territory_id"), table_name="basic_schools")
    op.drop_table("basic_schools")
