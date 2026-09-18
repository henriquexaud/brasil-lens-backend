"""Base declarativa do SQLAlchemy 2.0.

A convenção de nomes garante que constraints e índices criados pelos modelos e
pelas migrations tenham exatamente o mesmo nome — sem isso, o autogenerate do
Alembic produz diffs falsos e `DROP CONSTRAINT` fica dependente do nome sorteado
pelo PostgreSQL.
"""

from datetime import datetime

from sqlalchemy import MetaData, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """`created_at` responde "quando importamos"; `updated_at`, "quando revimos"."""

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
