"""ORM model package. Re-exports Base/metadata so Alembic can target app.models.

Models added in later phases MUST be imported here so they are registered on Base.metadata and
picked up by `alembic revision --autogenerate`.
"""

from app.models.base import Base

metadata = Base.metadata

__all__ = ["Base", "metadata"]
