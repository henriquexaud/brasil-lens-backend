from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PoliticalRelease(Base):
    __tablename__ = "political_releases"

    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("ingestion_runs.id"), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class PoliticalCandidate(Base):
    __tablename__ = "political_candidates"

    year: Mapped[int] = mapped_column(ForeignKey("political_releases.year"), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    office: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_code: Mapped[str] = mapped_column(String(9), nullable=False)
    elected_round: Mapped[int | None] = mapped_column(SmallInteger)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (Index("ix_political_candidates_scope", "year", "office", "scope_code"),)


class PoliticalResult(Base):
    __tablename__ = "political_results"

    year: Mapped[int] = mapped_column(ForeignKey("political_releases.year"), primary_key=True)
    office: Mapped[str] = mapped_column(String(24), primary_key=True)
    round: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    territory_code: Mapped[str] = mapped_column(
        String(9), ForeignKey("territories.ibge_code"), primary_key=True
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    __table_args__ = (Index("ix_political_results_scope", "year", "office", "round"),)
