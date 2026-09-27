from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import (
    BigInteger,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class WeatherProvider(str, enum.Enum):
    INMET = "inmet"
    CEMADEN = "cemaden"


class WeatherStationType(str, enum.Enum):
    AUTOMATIC_WEATHER = "automatic_weather"
    RAIN_GAUGE = "rain_gauge"


weather_provider_enum = Enum(
    WeatherProvider,
    name="weather_provider",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)
weather_station_type_enum = Enum(
    WeatherStationType,
    name="weather_station_type",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class WeatherStation(Base, TimestampMixin):
    __tablename__ = "weather_stations"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    provider: Mapped[WeatherProvider] = mapped_column(weather_provider_enum, nullable=False)
    external_code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    station_type: Mapped[WeatherStationType] = mapped_column(
        weather_station_type_enum, nullable=False
    )
    geom: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )
    state_abbreviation: Mapped[str | None] = mapped_column(String(4))

    observations: Mapped[list[WeatherObservation]] = relationship(
        back_populates="station",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("provider", "external_code", name="uq_weather_stations_provider_code"),
        Index("ix_weather_stations_geom", "geom", postgresql_using="gist"),
    )

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico
        return f"<WeatherStation {self.provider.value}:{self.external_code}>"


class WeatherObservation(Base, TimestampMixin):
    __tablename__ = "weather_observations"

    station_id: Mapped[int] = mapped_column(
        ForeignKey("weather_stations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    observed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)

    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    humidity_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    pressure_hpa: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    precipitation_mm: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))

    dataset_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ingestion_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
    )

    station: Mapped[WeatherStation] = relationship(back_populates="observations")

    __table_args__ = (
        Index(
            "ix_weather_observations_station_observed_at",
            "station_id",
            "observed_at",
            postgresql_include=[
                "temperature_c",
                "humidity_pct",
                "pressure_hpa",
                "precipitation_mm",
            ],
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico
        return f"<WeatherObservation station={self.station_id} at={self.observed_at}>"


class WeatherAlert(Base, TimestampMixin):
    __tablename__ = "weather_alerts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    provider: Mapped[WeatherProvider] = mapped_column(weather_provider_enum, nullable=False)
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)

    event: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    color: Mapped[str | None] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(String(200))

    onset: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    polygon: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
        nullable=False,
    )
    affected_ibge_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    risks: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    instructions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    dataset_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ingestion_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
    )

    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_weather_alerts_provider_external_id"),
        Index("ix_weather_alerts_polygon", "polygon", postgresql_using="gist"),
        Index("ix_weather_alerts_expires", "expires"),
    )

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico
        return f"<WeatherAlert {self.provider.value}:{self.external_id} {self.event}>"
