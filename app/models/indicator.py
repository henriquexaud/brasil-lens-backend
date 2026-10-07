from __future__ import annotations

import enum
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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class IndicatorOrigin(str, enum.Enum):
    SOURCED = "sourced"
    DERIVED = "derived"


indicator_origin_enum = Enum(
    IndicatorOrigin,
    name="socioeconomic_origin",
    values_callable=lambda cls: [item.value for item in cls],
)


class Indicator(Base, TimestampMixin):
    __tablename__ = "socioeconomic_indicators"

    id: Mapped[int] = mapped_column(SmallInteger, Identity(), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(24), nullable=False)
    origin: Mapped[IndicatorOrigin] = mapped_column(indicator_origin_enum, nullable=False)
    decimal_places: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class IndicatorValue(Base, TimestampMixin):
    __tablename__ = "socioeconomic_values"

    territory_id: Mapped[int] = mapped_column(
        ForeignKey("territories.id", ondelete="CASCADE"), primary_key=True
    )
    indicator_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("socioeconomic_indicators.id", ondelete="CASCADE"),
        primary_key=True,
    )
    reference_year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    dataset_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False
    )
    ingestion_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingestion_runs.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint("reference_year BETWEEN 1900 AND 2100", name="reference_year_range"),
        Index(
            "ix_socioeconomic_values_indicator_year",
            "indicator_id",
            "reference_year",
            postgresql_include=["territory_id", "value"],
        ),
        Index("ix_socioeconomic_values_dataset_id", "dataset_id"),
    )
