"""SQLAlchemy declarative base + shared metadata for all ORM models.

No tables are defined in this phase (data-model.md). Models in later phases subclass Base so
they register on Base.metadata, which is Alembic's autogenerate target. The base must therefore
exist now, even while empty.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base; its `.metadata` is the target Alembic autogenerates against."""
