from __future__ import annotations

import enum

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class TerritoryLevel(str, enum.Enum):
    COUNTRY = "country"
    REGION = "region"
    STATE = "state"
    MUNICIPALITY = "municipality"


class GeometryLOD(str, enum.Enum):
    CANONICAL = "canonical"
    OVERVIEW = "overview"
    DETAIL = "detail"


REQUIRES_PARENT: frozenset[TerritoryLevel] = frozenset({TerritoryLevel.MUNICIPALITY})

EXPECTED_PARENT_LEVEL: dict[TerritoryLevel, TerritoryLevel] = {
    TerritoryLevel.REGION: TerritoryLevel.COUNTRY,
    TerritoryLevel.STATE: TerritoryLevel.REGION,
    TerritoryLevel.MUNICIPALITY: TerritoryLevel.STATE,
}


territory_level_enum = Enum(
    TerritoryLevel,
    name="territory_level",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)
geometry_lod_enum = Enum(
    GeometryLOD,
    name="geometry_lod",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Territory(Base, TimestampMixin):
    __tablename__ = "territories"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    level: Mapped[TerritoryLevel] = mapped_column(territory_level_enum, nullable=False)

    ibge_code: Mapped[str] = mapped_column(String(9), nullable=False, unique=True)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    abbreviation: Mapped[str | None] = mapped_column(String(4))

    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("territories.id", ondelete="RESTRICT", name="fk_territories_parent_id"),
    )
    capital_territory_id: Mapped[int | None] = mapped_column(
        ForeignKey("territories.id", ondelete="SET NULL", name="fk_territories_capital_id"),
    )

    bbox_west: Mapped[float | None] = mapped_column()
    bbox_south: Mapped[float | None] = mapped_column()
    bbox_east: Mapped[float | None] = mapped_column()
    bbox_north: Mapped[float | None] = mapped_column()

    parent: Mapped[Territory | None] = relationship(
        remote_side=[id],
        foreign_keys=[parent_id],
        back_populates="children",
    )
    children: Mapped[list[Territory]] = relationship(
        back_populates="parent",
        foreign_keys=[parent_id],
    )
    capital: Mapped[Territory | None] = relationship(
        remote_side=[id],
        foreign_keys=[capital_territory_id],
    )
    geometries: Mapped[list[TerritoryGeometry]] = relationship(
        back_populates="territory",
        cascade="all, delete-orphan",
    )

    normalized_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    normalized_abbreviation: Mapped[str | None] = mapped_column(String(4), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(level = 'country' AND parent_id IS NULL) "
            "OR (level <> 'country' AND parent_id IS NOT NULL)",
            name="country_is_root",
        ),
        Index("ix_territories_level_name", "level", "name"),
        Index("ix_territories_parent_id_name", "parent_id", "name"),
        Index("ix_territories_normalized_name", "normalized_name"),
        Index("ix_territories_normalized_abbr", "normalized_abbreviation"),
    )

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico
        return f"<Territory {self.level.value}:{self.ibge_code} {self.name!r}>"


class TerritoryGeometry(Base, TimestampMixin):
    __tablename__ = "territory_geometries"

    territory_id: Mapped[int] = mapped_column(
        ForeignKey("territories.id", ondelete="CASCADE"),
        primary_key=True,
    )
    lod: Mapped[GeometryLOD] = mapped_column(geometry_lod_enum, primary_key=True)

    geom: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
        nullable=False,
    )

    simplify_tolerance: Mapped[float | None] = mapped_column()
    vertex_count: Mapped[int] = mapped_column(Integer, nullable=False)

    dataset_id: Mapped[int | None] = mapped_column(
        SmallInteger,
        ForeignKey("datasets.id", ondelete="SET NULL"),
    )

    territory: Mapped[Territory] = relationship(back_populates="geometries")

    __table_args__ = (Index("ix_territory_geometries_geom", "geom", postgresql_using="gist"),)
