from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020_places"
down_revision: str | None = "0019_basic_schools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Lugares dos contextos Natureza, Recursos e Esportes (ADR-25): um ponto por
    # feição (e o contorno, quando o dado é uma área), com o município já
    # resolvido na ingestão. Cada camada é regravada inteira.
    op.create_table(
        "places",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("context", sa.String(16), nullable=False),
        sa.Column("layer", sa.String(24), nullable=False),
        sa.Column("category", sa.String(32)),
        # Outras categorias em que o lugar também aparece (a quadra poliesportiva
        # entra no filtro de cada esporte que ela recebe).
        sa.Column("extra_categories", postgresql.ARRAY(sa.String(32))),
        sa.Column("external_id", sa.String(64)),
        sa.Column("name", sa.String(200)),
        sa.Column("summary", sa.String(200)),
        sa.Column(
            "attributes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("weight", sa.Float()),
        sa.Column("municipality_code", sa.String(7)),
        sa.Column(
            "point",
            geoalchemy2.Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column(
            "shape",
            geoalchemy2.Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False),
        ),
        sa.Column(
            "dataset_id",
            sa.SmallInteger(),
            sa.ForeignKey("datasets.id", ondelete="SET NULL"),
        ),
        sa.CheckConstraint(
            "context IN ('nature', 'resources', 'sports')", name=op.f("ck_places_context")
        ),
    )
    op.create_index("ix_places_point", "places", ["point"], postgresql_using="gist")
    op.create_index("ix_places_shape", "places", ["shape"], postgresql_using="gist")
    op.create_index("ix_places_layer_category", "places", ["layer", "category"])
    op.create_index("ix_places_layer_municipality", "places", ["layer", "municipality_code"])
    # Parte de cada município e UF em cada bioma, calculada na ingestão.
    op.create_table(
        "territory_biomes",
        sa.Column("territory_code", sa.String(9), primary_key=True),
        sa.Column("biome", sa.String(32), primary_key=True),
        sa.Column("share", sa.Numeric(5, 4), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("territory_biomes")
    op.drop_index("ix_places_layer_municipality", table_name="places")
    op.drop_index("ix_places_layer_category", table_name="places")
    op.drop_index("ix_places_shape", table_name="places")
    op.drop_index("ix_places_point", table_name="places")
    op.drop_table("places")
