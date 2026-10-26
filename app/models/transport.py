from __future__ import annotations

from decimal import Decimal
from typing import Any

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import BigInteger, ForeignKey, Identity, Index, Numeric, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

TRANSPORT_LAYERS = ("road_federal", "road_state", "road_local", "rail", "waterway", "port")


class TransportFeature(Base):
    __tablename__ = "transport_features"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    layer: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(String(160))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    geom: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False), nullable=False
    )
    dataset_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("datasets.id", ondelete="SET NULL")
    )

    __table_args__ = (
        Index("ix_transport_features_geom", "geom", postgresql_using="gist"),
        Index("ix_transport_features_layer", "layer"),
    )


class TransportOverview(Base):
    __tablename__ = "transport_overviews"

    layer: Mapped[str] = mapped_column(String(16), primary_key=True)
    geom: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False), nullable=False
    )


class TransportTotal(Base):
    __tablename__ = "transport_totals"

    territory_id: Mapped[int] = mapped_column(
        ForeignKey("territories.id", ondelete="CASCADE"), primary_key=True
    )
    layer: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric(14, 1), nullable=False)
