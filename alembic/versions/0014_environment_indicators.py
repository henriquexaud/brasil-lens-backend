from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_environment_indicators"
down_revision: str | None = "0013_political"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # O motor de indicadores passa a servir mais de um contexto (ADR-15). As
    # tabelas mantêm o nome `socioeconomic_*`: renomear não traz ganho funcional.
    op.add_column(
        "socioeconomic_indicators",
        sa.Column(
            "context", sa.String(24), nullable=False, server_default=sa.text("'socioeconomic'")
        ),
    )
    op.create_check_constraint(
        op.f("ck_socioeconomic_indicators_context"),
        "socioeconomic_indicators",
        "context IN ('socioeconomic', 'environment')",
    )
    origin = postgresql.ENUM("sourced", "derived", name="socioeconomic_origin", create_type=False)
    table = sa.table(
        "socioeconomic_indicators",
        sa.column("key"),
        sa.column("name"),
        sa.column("description"),
        sa.column("unit"),
        sa.column("origin", origin),
        sa.column("decimal_places"),
        sa.column("display_order"),
        sa.column("context"),
    )
    op.bulk_insert(
        table,
        [
            {
                "key": "deforestation_prodes",
                "name": "Desmatamento anual",
                "unit": "km2",
                "description": "Área de vegetação nativa suprimida no ano PRODES (agosto a julho), somando os seis biomas monitorados pelo INPE.",
                "origin": "sourced",
                "decimal_places": 2,
                "display_order": 110,
                "context": "environment",
            },
            {
                "key": "forest_cover",
                "name": "Cobertura florestal",
                "unit": "%",
                "description": "Parte da área do território coberta por floresta (formação florestal, savânica, mangue, floresta alagável e restinga arbórea), segundo a Coleção 11 do MapBiomas.",
                "origin": "sourced",
                "decimal_places": 1,
                "display_order": 120,
                "context": "environment",
            },
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM socioeconomic_indicators "
        "WHERE key IN ('deforestation_prodes', 'forest_cover')"
    )
    op.drop_constraint(
        op.f("ck_socioeconomic_indicators_context"), "socioeconomic_indicators", type_="check"
    )
    op.drop_column("socioeconomic_indicators", "context")
