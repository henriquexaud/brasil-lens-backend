"""Estações meteorológicas, leituras e alertas.

Uma leitura nasce num ponto (estação) e um alerta pode cobrir uma área; ambos
possuem identidade, tempo e geografia próprios.

`datasets` e `ingestion_runs` são reaproveitados sem alteração: proveniência e
frescor por fonte já são exatamente o que essas tabelas registram.
"""

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
    """Fonte de um registro meteorológico. Um valor novo = uma fonte nova."""

    INMET = "inmet"
    CEMADEN = "cemaden"


class WeatherStationType(str, enum.Enum):
    """O que a estação mede. Uma estação de chuva não preenche temperatura."""

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
    """Uma estação/pluviômetro físico, identificado pela fonte."""

    __tablename__ = "weather_stations"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    provider: Mapped[WeatherProvider] = mapped_column(weather_provider_enum, nullable=False)
    # Código da estação na fonte (ex.: "A001" no INMET) — não é único sozinho:
    # fontes diferentes podem reusar o mesmo formato de código por coincidência.
    external_code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    station_type: Mapped[WeatherStationType] = mapped_column(
        weather_station_type_enum, nullable=False
    )
    geom: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )
    # UF onde a estação fica — conveniência de exibição; não é FK para
    # `territories`. Uma estação não pertence a um território no sentido do
    # modelo territorial (ver docstring do módulo).
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
    """Uma leitura de uma estação, num instante.

    PK `(station_id, observed_at)`: o job pode reexecutar sobre o mesmo
    intervalo sem duplicar linha, só atualizar `updated_at`.
    """

    __tablename__ = "weather_observations"

    station_id: Mapped[int] = mapped_column(
        ForeignKey("weather_stations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Horário da leitura *na fonte* (UTC) — não o horário em que ingerimos.
    observed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)

    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    humidity_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    pressure_hpa: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    # Chuva acumulada no intervalo de leitura da fonte (não é um total diário).
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
        # A leitura mais recente por estação — a única consulta que
        # `GET /weather/stations` faz. INCLUDE evita ida à tabela.
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
    """Um alerta georreferenciado — aviso meteorológico (INMET) ou risco
    geo-hidrológico (CEMADEN). Mesma tabela, mesmas colunas: as duas fontes já
    produzem o mesmo `WeatherAlertRecord` (`app/providers/records.py`), então
    não há necessidade de coluna condicional por proveniência. `category` e
    `severity_level` — a classificação comum que o frontend consome — são
    derivados de `provider`/`severity` em `services/weather.py`, não
    persistidos aqui: hoje são 100% função de `provider`, guardá-los seria
    duplicar dado sem necessidade concreta (mesmo raciocínio de
    `docs/ARCHITECTURE.md` §10.4 contra especular estrutura sem fonte real).

    `polygon` é sempre gravado como MULTIPOLYGON, mesmo quando a fonte manda um
    único Polygon — mesma normalização (`ST_Multi`) que `territory_geometries`
    já aplica à malha do IBGE, pelo mesmo motivo: um tipo de coluna só.
    """

    __tablename__ = "weather_alerts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    provider: Mapped[WeatherProvider] = mapped_column(weather_provider_enum, nullable=False)
    # Chave estável da fonte (para o INMET, `codigo`; para o CEMADEN,
    # `id_alerta` — sobrevive a uma atualização do mesmo alerta; ver
    # app/providers/inmet/alerts.py e app/providers/cemaden/alerts.py).
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)

    event: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    # Cor oficial da fonte (ex.: "#FFC800" do INMET) — exibida como está,
    # nunca substituída pela paleta do produto (é convenção de severidade da
    # própria fonte, não um dado sequencial/categórico nosso).
    color: Mapped[str | None] = mapped_column(String(16))
    # Frase livre opcional além de `event` (ex.: "BLUMENAU/SC" do CEMADEN,
    # onde `event` sozinho — "Movimentos de Massa" — não diz o município).
    # Texto da fonte, verbatim, pela mesma razão de `color`: não é nosso papel
    # re-capitalizar topônimo (ver app/providers/cemaden/alerts.py). NULL para
    # fontes onde `event`/`risks` já bastam (o INMET, hoje).
    description: Mapped[str | None] = mapped_column(String(200))

    onset: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    polygon: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
        nullable=False,
    )
    # Metadado de exibição, não integridade referencial: mesmo raciocínio de
    # `ingestion_runs.details` (jsonb ao lado de uma FK real seria redundante
    # aqui, já que nenhuma consulta precisa fazer JOIN por município afetado).
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
        # `GET /weather/alerts` filtra por "ainda ativo" (expires > now()).
        Index("ix_weather_alerts_expires", "expires"),
    )

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico
        return f"<WeatherAlert {self.provider.value}:{self.external_id} {self.event}>"
