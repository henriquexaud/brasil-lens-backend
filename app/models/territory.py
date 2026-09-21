"""Território e geometria.

Decisão central do modelo: **uma única entidade territorial** com `level` e
`parent_id` auto-referente, em vez de tabelas por nível. O motivo determinante é
que `indicator_values` passa a ter uma FK real para território — a alternativa
(`territory_type` + `territory_id` polimórfico) não tem integridade referencial.
Ver docs/ARCHITECTURE.md §4.1.
"""

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
    """Níveis territoriais suportados.

    Adicionar um nível (mesorregião, distrito, região metropolitana) é adicionar
    um valor aqui + ingestão. Modelo, mapa e API já são genéricos por nível.
    """

    COUNTRY = "country"
    REGION = "region"
    STATE = "state"
    MUNICIPALITY = "municipality"


class GeometryLOD(str, enum.Enum):
    """Níveis de detalhe geométrico.

    `CANONICAL` é a malha oficial do IBGE, servida em páginas pequenas no clima.
    Os demais são derivados na ingestão (ver docs/ARCHITECTURE.md §5).
    """

    CANONICAL = "canonical"
    OVERVIEW = "overview"
    DETAIL = "detail"


# ---------------------------------------------------------------------------
# A forma da hierarquia, declarada uma vez.
#
# Estes dois mapas são o que a API precisa saber para aceitar ou recusar um
# recorte, e ficam aqui — ao lado do enum — porque é isto que torna verdadeira a
# promessa do docstring acima: adicionar um nível territorial é editar *um*
# arquivo. Enquanto viviam dentro de cada serviço, `/map` e `/views` podiam
# divergir sobre quem pode ser pai de quem.
# ---------------------------------------------------------------------------

# Níveis que exigem recorte por pai. Sem esta regra, uma requisição a
# `level=municipality` devolveria a malha municipal do país inteiro.
REQUIRES_PARENT: frozenset[TerritoryLevel] = frozenset({TerritoryLevel.MUNICIPALITY})

# Pai aceitável para cada nível — evita pedidos sem sentido como "municípios da
# região Sudeste" (o pai de um município é uma UF).
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

    # Identificador canônico externo. É ele que aparece na API pública — os IDs
    # internos nunca vazam no contrato.
    ibge_code: Mapped[str] = mapped_column(String(9), nullable=False, unique=True)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Sigla da UF ("SP") ou da região ("SE"). Nulo para país e municípios.
    abbreviation: Mapped[str | None] = mapped_column(String(4))

    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("territories.id", ondelete="RESTRICT", name="fk_territories_parent_id"),
    )
    # A capital de uma UF *é* um município: FK preserva o código IBGE dela.
    capital_territory_id: Mapped[int | None] = mapped_column(
        ForeignKey("territories.id", ondelete="SET NULL", name="fk_territories_capital_id"),
    )

    # Extensão geográfica, gravada na ingestão de geometria. Permite ao mapa dar
    # fitBounds no drill-down sem ler uma única geometria.
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

    # Campos normalizados (minúsculo e sem diacríticos) para busca instantânea indexada
    normalized_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    normalized_abbreviation: Mapped[str | None] = mapped_column(String(4), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(level = 'country' AND parent_id IS NULL) "
            "OR (level <> 'country' AND parent_id IS NOT NULL)",
            name="country_is_root",
        ),
        # Listagens por nível, ordenadas por nome (`/territories?level=state`).
        Index("ix_territories_level_name", "level", "name"),
        # Filhos de um território (drill-down, contagem de municípios).
        Index("ix_territories_parent_id_name", "parent_id", "name"),
        # Busca textual normalizada e insensível a acentos
        Index("ix_territories_normalized_name", "normalized_name"),
        Index("ix_territories_normalized_abbr", "normalized_abbreviation"),
    )

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico
        return f"<Territory {self.level.value}:{self.ibge_code} {self.name!r}>"


class TerritoryGeometry(Base, TimestampMixin):
    """Uma geometria por território por nível de detalhe.

    Tabela separada (e não colunas `geom_overview`/`geom_detail`) porque assim
    adicionar um LOD é dado, não migration, e a query do mapa continua sendo
    `WHERE lod = :lod`.
    """

    __tablename__ = "territory_geometries"

    territory_id: Mapped[int] = mapped_column(
        ForeignKey("territories.id", ondelete="CASCADE"),
        primary_key=True,
    )
    lod: Mapped[GeometryLOD] = mapped_column(geometry_lod_enum, primary_key=True)

    # MultiPolygon é obrigatório: a malha do IBGE mistura Polygon e MultiPolygon
    # (ilhas e exclaves). A ingestão normaliza tudo com ST_Multi.
    geom: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
        nullable=False,
    )

    # Nulo em CANONICAL; preenchido nos LODs derivados para tornar a
    # simplificação reproduzível e auditável.
    simplify_tolerance: Mapped[float | None] = mapped_column()
    vertex_count: Mapped[int] = mapped_column(Integer, nullable=False)

    dataset_id: Mapped[int | None] = mapped_column(
        SmallInteger,
        ForeignKey("datasets.id", ondelete="SET NULL"),
    )

    territory: Mapped[Territory] = relationship(back_populates="geometries")

    __table_args__ = (
        # Índice espacial: pré-requisito de qualquer predicado geográfico
        # (viewport, point-in-polygon, vizinhança).
        Index("ix_territory_geometries_geom", "geom", postgresql_using="gist"),
    )
