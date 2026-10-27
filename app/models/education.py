from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

EDUCATION_CATEGORIES = ("federal", "state", "municipal")
EDUCATION_ORGANIZATIONS = (
    "university",
    "university_center",
    "college",
    "federal_institute",
    "cefet",
)


class EducationInstitution(Base):
    """Instituição pública de ensino superior num ano do Censo (ADR-19)."""

    __tablename__ = "education_institutions"

    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    acronym: Mapped[str | None] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    organization: Mapped[str] = mapped_column(String(24), nullable=False)
    seat_territory_id: Mapped[int | None] = mapped_column(
        ForeignKey("territories.id", ondelete="SET NULL")
    )
    faculty: Mapped[int | None] = mapped_column(Integer)
    staff: Mapped[int | None] = mapped_column(Integer)
    distance_enrollment: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("category IN ('federal', 'state', 'municipal')", name="category"),
        CheckConstraint(
            "organization IN ('university', 'university_center', 'college', "
            "'federal_institute', 'cefet')",
            name="organization",
        ),
    )


class EducationOffer(Base):
    """Cursos presenciais de graduação de uma instituição num município."""

    __tablename__ = "education_offers"

    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    institution_code: Mapped[int] = mapped_column(Integer, primary_key=True)
    territory_id: Mapped[int] = mapped_column(
        ForeignKey("territories.id", ondelete="CASCADE"), primary_key=True
    )
    courses: Mapped[int] = mapped_column(Integer, nullable=False)
    enrollment: Mapped[int] = mapped_column(Integer, nullable=False)
    entrants: Mapped[int] = mapped_column(Integer, nullable=False)
    graduates: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["year", "institution_code"],
            ["education_institutions.year", "education_institutions.code"],
            ondelete="CASCADE",
        ),
        Index("ix_education_offers_year_territory", "year", "territory_id"),
    )
