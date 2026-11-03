from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_user_consent"
down_revision: str | None = "0017_education"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Prova do aceite da Política de Privacidade e dos Termos de Uso: qual versão e quando.
    # Contas anteriores ficam nulas e aceitam pelo aviso do app.
    op.add_column("users", sa.Column("terms_version", sa.String(16)))
    op.add_column("users", sa.Column("terms_accepted_at", sa.TIMESTAMP(timezone=True)))


def downgrade() -> None:
    op.drop_column("users", "terms_accepted_at")
    op.drop_column("users", "terms_version")
