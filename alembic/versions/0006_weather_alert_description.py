"""Coluna `description` em `weather_alerts` — texto livre opcional da fonte.

Necessária para o CEMADEN: `event` sozinho ("Movimentos de Massa") não diz o
município, ao contrário do INMET onde `event` já é a frase completa. Ver
`app/models/weather.py` e `app/providers/cemaden/alerts.py`. Nula para
qualquer alerta existente (INMET) — nenhum dado precisa de backfill.

Revision ID: 0006_weather_alert_description
Revises: 0005_territory_search
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_weather_alert_description"
down_revision: str | None = "0005_territory_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "weather_alerts", sa.Column("description", sa.String(length=200), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("weather_alerts", "description")
