"""Visualização salva: um recorte do mapa que o usuário quis guardar.

É a única entidade **escrita pelo usuário** no sistema — todo o resto entra pela
ingestão. Por isso ela mora em uma tabela própria e não referencia `territories`
nem `indicators` por FK: uma visualização é um *marcador de navegação*
(`level` + `parent` + `indicator` + `year`), e amarrá-la por FK faria uma
reingestão que renomeia um indicador apagar a visualização do usuário em
cascata. A existência do território e do indicador é validada na escrita, no
serviço, onde a mensagem de erro pode ser útil.

O identificador público é um UUID: como em todo o resto da API, o id interno
não aparece no contrato.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Identity, Index, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.territory import TerritoryLevel, territory_level_enum


class SavedView(Base, TimestampMixin):
    __tablename__ = "saved_views"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)

    # Identificador público e estável. UUID (e não o id serial) porque é ele que
    # aparece na URL de PUT/DELETE.
    public_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        nullable=False,
        unique=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)

    # --- o recorte propriamente dito -------------------------------------
    level: Mapped[TerritoryLevel] = mapped_column(territory_level_enum, nullable=False)
    # Código IBGE do pai (ex.: "35" para os municípios de SP). Nulo no mapa do país.
    parent_code: Mapped[str | None] = mapped_column(String(9))
    indicator_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # Nulo significa "último ano disponível" — o mesmo `latest` da rota do mapa.
    # Guardar o sentinela como NULL mantém a coluna numérica e comparável.
    reference_year: Mapped[int | None] = mapped_column(SmallInteger)
    classes: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=5)

    __table_args__ = (
        CheckConstraint(
            "reference_year IS NULL OR reference_year BETWEEN 1900 AND 2100",
            name="saved_view_year_range",
        ),
        CheckConstraint("classes BETWEEN 2 AND 9", name="saved_view_class_range"),
        # Mesma regra que a rota do mapa aplica: municípios exigem um pai. Aqui
        # ela é do banco, para que nenhuma visualização impossível de abrir
        # possa ser gravada — nem por script, nem por migração futura.
        CheckConstraint(
            "level <> 'municipality' OR parent_code IS NOT NULL",
            name="saved_view_municipality_needs_parent",
        ),
        CheckConstraint("length(btrim(name)) > 0", name="saved_view_name_not_blank"),
        # A listagem é sempre "mais recentes primeiro".
        Index("ix_saved_views_created_at", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico
        return f"<SavedView {self.public_id} {self.name!r}>"
