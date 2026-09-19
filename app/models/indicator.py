"""Catálogo de indicadores, proveniência (datasets) e a série de valores.

O núcleo do modelo está em `IndicatorValue`: a PK composta
`(territory_id, indicator_id, reference_year)` é a chave natural do domínio
"território ↔ indicador ↔ ano ↔ valor" e é o que garante a idempotência da
ingestão. Ver docs/ARCHITECTURE.md §4.2.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.context import DataContext

data_context_enum = Enum(
    DataContext,
    name="data_context",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class IndicatorOrigin(str, enum.Enum):
    """`SOURCED` vem de uma fonte externa; `DERIVED` é calculado na ingestão."""

    SOURCED = "sourced"
    DERIVED = "derived"


indicator_origin_enum = Enum(
    IndicatorOrigin,
    name="indicator_origin",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Indicator(Base, TimestampMixin):
    __tablename__ = "indicators"

    id: Mapped[int] = mapped_column(SmallInteger, Identity(), primary_key=True)
    # Identificador público e estável: é `key` que aparece na API, não o id.
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Unidade da grandeza já normalizada ("people", "km2", "people/km2", "BRL").
    unit: Mapped[str] = mapped_column(String(24), nullable=False)
    origin: Mapped[IndicatorOrigin] = mapped_column(
        indicator_origin_enum,
        nullable=False,
        default=IndicatorOrigin.SOURCED,
    )
    # Agrupamento temático (sociopolítico, clima/ambiente, biodiversidade).
    # Todo indicador de hoje é sociopolítico — o default cobre isso sem exigir
    # que cada seed declare o óbvio. Ver app/models/context.py.
    context: Mapped[DataContext] = mapped_column(
        data_context_enum,
        nullable=False,
        default=DataContext.SOCIOPOLITICAL,
    )
    # Metadado de formatação (quantas casas exibir), não de aparência/cor.
    decimal_places: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=100)

    values: Mapped[list[IndicatorValue]] = relationship(
        back_populates="indicator",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("ix_indicators_context", "context"),)

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico
        return f"<Indicator {self.key}>"


class Dataset(Base, TimestampMixin):
    """Proveniência de um conjunto de valores.

    Fica no *valor*, não no indicador: o mesmo indicador vem de datasets
    diferentes conforme o período (população 2022 = Censo; 2024 = estimativa).
    """

    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(SmallInteger, Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    # Ex.: "agregados/4714/v/93", "malhas/v3", "derived/population_density".
    code: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    # Quando a *fonte* publicou/atualizou, quando ela informa isso.
    source_updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    __table_args__ = (UniqueConstraint("source", "code", name="uq_datasets_source_code"),)

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico
        return f"<Dataset {self.source}:{self.code}>"


class IndicatorValue(Base, TimestampMixin):
    """Um valor de um indicador, para um território, em um ano de referência.

    Ausência de linha significa ausência de dado — não existe linha com valor
    nulo. Isso mantém `MAX(reference_year)` honesto (um ano só é "disponível" se
    houver dado) e dispensa filtros extras nas estatísticas do mapa.
    """

    __tablename__ = "indicator_values"

    territory_id: Mapped[int] = mapped_column(
        ForeignKey("territories.id", ondelete="CASCADE"),
        primary_key=True,
    )
    indicator_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("indicators.id", ondelete="CASCADE"),
        primary_key=True,
    )
    reference_year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)

    # Numeric (e não float) porque aqui convivem população (inteiro grande),
    # PIB em reais (muito grande) e densidade (fracionária).
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)

    dataset_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ingestion_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
    )

    indicator: Mapped[Indicator] = relationship(back_populates="values")
    dataset: Mapped[Dataset] = relationship()

    __table_args__ = (
        CheckConstraint(
            "reference_year BETWEEN 1900 AND 2100",
            name="reference_year_range",
        ),
        # Índice ditado pela query do mapa: filtra por indicador + ano e precisa
        # de território e valor. O INCLUDE torna a leitura index-only.
        Index(
            "ix_indicator_values_indicator_id_reference_year",
            "indicator_id",
            "reference_year",
            postgresql_include=["territory_id", "value"],
        ),
        # Overview e séries históricas de um território já são servidos pela PK
        # (territory_id, indicator_id, reference_year).
        Index("ix_indicator_values_dataset_id", "dataset_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico
        return (
            f"<IndicatorValue t={self.territory_id} i={self.indicator_id} "
            f"y={self.reference_year} v={self.value}>"
        )
