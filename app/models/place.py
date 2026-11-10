from __future__ import annotations

from decimal import Decimal
from typing import Any

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import BigInteger, Float, ForeignKey, Identity, Index, Numeric, SmallInteger, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PLACE_CONTEXTS = ("nature", "resources", "sports")


class Place(Base):
    """Feição pontual ou de área dos contextos Natureza, Recursos e Esportes (ADR-25)."""

    __tablename__ = "places"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    context: Mapped[str] = mapped_column(String(16), nullable=False)
    layer: Mapped[str] = mapped_column(String(24), nullable=False)
    category: Mapped[str | None] = mapped_column(String(32))
    extra_categories: Mapped[list[str] | None] = mapped_column(ARRAY(String(32)))
    external_id: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(String(200))
    attributes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    weight: Mapped[float | None] = mapped_column(Float)
    municipality_code: Mapped[str | None] = mapped_column(String(7))
    point: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    )
    shape: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False)
    )
    dataset_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("datasets.id", ondelete="SET NULL")
    )

    __table_args__ = (
        Index("ix_places_point", "point", postgresql_using="gist"),
        Index("ix_places_shape", "shape", postgresql_using="gist"),
        Index("ix_places_layer_category", "layer", "category"),
        Index("ix_places_layer_municipality", "layer", "municipality_code"),
    )


class TerritoryBiome(Base):
    __tablename__ = "territory_biomes"

    territory_code: Mapped[str] = mapped_column(String(9), primary_key=True)
    biome: Mapped[str] = mapped_column(String(32), primary_key=True)
    share: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
