from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_transport"
down_revision: str | None = "0014_environment_indicators"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LAYERS = "layer IN ('road_federal', 'road_state', 'road_local', 'rail', 'waterway', 'port')"


def upgrade() -> None:
    # Malha de transporte (ADR-17): trechos e portos simplificados na ingestão,
    # um traçado nacional já fundido por camada e as extensões por território.
    op.create_table(
        "transport_features",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("name", sa.String(160)),
        sa.Column(
            "attributes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column(
            "dataset_id",
            sa.SmallInteger(),
            sa.ForeignKey("datasets.id", ondelete="SET NULL"),
        ),
        sa.CheckConstraint(LAYERS, name=op.f("ck_transport_features_layer")),
    )
    op.create_index(
        "ix_transport_features_geom", "transport_features", ["geom"], postgresql_using="gist"
    )
    op.create_index("ix_transport_features_layer", "transport_features", ["layer"])
    op.create_table(
        "transport_overviews",
        sa.Column("layer", sa.String(16), primary_key=True),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.CheckConstraint(LAYERS, name=op.f("ck_transport_overviews_layer")),
    )
    op.create_table(
        "transport_totals",
        sa.Column(
            "territory_id",
            sa.Integer(),
            sa.ForeignKey("territories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("layer", sa.String(16), primary_key=True),
        sa.Column("value", sa.Numeric(14, 1), nullable=False),
        sa.CheckConstraint(LAYERS, name=op.f("ck_transport_totals_layer")),
    )


def downgrade() -> None:
    op.drop_table("transport_totals")
    op.drop_table("transport_overviews")
    op.drop_index("ix_transport_features_layer", table_name="transport_features")
    op.drop_index("ix_transport_features_geom", table_name="transport_features")
    op.drop_table("transport_features")
