from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_transport_tiles"
down_revision: str | None = "0015_transport"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tiles vetoriais do OpenStreetMap já prontos (ADR-18): a API só os lê pela
    # chave, sem processar geometria no request.
    op.create_table(
        "transport_tiles",
        sa.Column("zoom", sa.SmallInteger(), primary_key=True),
        sa.Column("x", sa.Integer(), primary_key=True),
        sa.Column("y", sa.Integer(), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("transport_tiles")
