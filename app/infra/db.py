"""SQLAlchemy engine + session factory + readiness ping for Postgres.

The engine and session factory live here in infra/; only repo/ and Alembic use them to touch
the database. ping() backs the /health readiness check.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


class Database:
    """Holds a SQLAlchemy engine and a session factory bound to it."""

    def __init__(self, url: str) -> None:
        """Create the engine with pool_pre_ping so stale connections are detected up front."""
        self._engine: Engine = create_engine(url, pool_pre_ping=True, future=True)
        self._session_factory = sessionmaker(
            bind=self._engine, class_=Session, expire_on_commit=False
        )

    @property
    def engine(self) -> Engine:
        """Expose the engine (used by Alembic / migrations)."""
        return self._engine

    def session(self) -> Session:
        """Return a new ORM session; the caller is responsible for its lifecycle."""
        return self._session_factory()

    def ping(self) -> bool:
        """Return True when a trivial 'SELECT 1' succeeds, else False (no raise).

        Used by /health to report Postgres reachability without leaking the underlying error.
        """
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def dispose(self) -> None:
        """Close all pooled connections on shutdown."""
        self._engine.dispose()
