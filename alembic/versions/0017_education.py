from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_education"
down_revision: str | None = "0016_transport_tiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORIES = "category IN ('federal', 'state', 'municipal')"
ORGANIZATIONS = (
    "organization IN ('university', 'university_center', 'college', 'federal_institute', 'cefet')"
)


def upgrade() -> None:
    # Educação (ADR-19): matrículas no motor de indicadores e, à parte, as
    # instituições públicas de cada ano e onde elas oferecem cursos presenciais.
    op.drop_constraint(
        op.f("ck_socioeconomic_indicators_context"), "socioeconomic_indicators", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_socioeconomic_indicators_context"),
        "socioeconomic_indicators",
        "context IN ('socioeconomic', 'environment', 'education')",
    )
    op.create_table(
        "education_institutions",
        sa.Column("year", sa.SmallInteger(), primary_key=True),
        sa.Column("code", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("acronym", sa.String(20)),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("organization", sa.String(24), nullable=False),
        sa.Column(
            "seat_territory_id",
            sa.Integer(),
            sa.ForeignKey("territories.id", ondelete="SET NULL"),
        ),
        sa.Column("faculty", sa.Integer()),
        sa.Column("staff", sa.Integer()),
        sa.Column("distance_enrollment", sa.Integer(), nullable=False),
        sa.Column(
            "dataset_id",
            sa.SmallInteger(),
            sa.ForeignKey("datasets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.CheckConstraint(CATEGORIES, name=op.f("ck_education_institutions_category")),
        sa.CheckConstraint(ORGANIZATIONS, name=op.f("ck_education_institutions_organization")),
    )
    op.create_table(
        "education_offers",
        sa.Column("year", sa.SmallInteger(), primary_key=True),
        sa.Column("institution_code", sa.Integer(), primary_key=True),
        sa.Column(
            "territory_id",
            sa.Integer(),
            sa.ForeignKey("territories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("courses", sa.Integer(), nullable=False),
        sa.Column("enrollment", sa.Integer(), nullable=False),
        sa.Column("entrants", sa.Integer(), nullable=False),
        sa.Column("graduates", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["year", "institution_code"],
            ["education_institutions.year", "education_institutions.code"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_education_offers_year_territory", "education_offers", ["year", "territory_id"])


def downgrade() -> None:
    op.drop_index("ix_education_offers_year_territory", table_name="education_offers")
    op.drop_table("education_offers")
    op.drop_table("education_institutions")
    op.execute(
        "DELETE FROM socioeconomic_indicators WHERE context = 'education'"
    )
    op.drop_constraint(
        op.f("ck_socioeconomic_indicators_context"), "socioeconomic_indicators", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_socioeconomic_indicators_context"),
        "socioeconomic_indicators",
        "context IN ('socioeconomic', 'environment')",
    )
