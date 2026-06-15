"""DB-backed fixtures for the cook-facing integration tests.

These exercise the real request → repo → wall → recipe_view path against a real Postgres (the ARRAY /
UUID columns are Postgres-specific, so SQLite is not a substitute). The DB URL comes from
TEST_DATABASE_URL or POSTGRES_URL, defaulting to the docker-compose Postgres on localhost; if no
Postgres is reachable the whole module is skipped so `make test` stays green without the stack.

Isolation uses the SQLAlchemy "join an external transaction" recipe: every test runs inside one outer
transaction that is rolled back at teardown, with a SAVEPOINT that restarts on each request's commit —
so repos behave exactly as in production (commits succeed, data persists across requests within a test)
yet nothing leaks between tests or into the real database.
"""

from __future__ import annotations

import os
import types
import uuid
from collections.abc import Callable, Iterator

import pytest
from app.api.deps import get_db
from app.api.health import register_health_router
from app.api.user import register_user_routers
from app.core import security
from app.core.errors import register_error_handlers
from app.models import Base
from app.models.cook_account import CookAccount
from app.repo import cook_accounts as cook_repo
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_DEFAULT_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/souschef"

# Cook-session signing material for the test app (009). The fake Vault below hands this same key to
# `require_cook`/the /chat limiter, and the `auth` fixture signs its tokens with it, so a minted token
# verifies end to end without a real Vault.
_TEST_COOK_SIGNING_KEY = "test-cook-session-signing-key-0123456789"  # noqa: S105 — a test fixture key
_TEST_COOK_TTL_MINUTES = 480
# One bcrypt hash computed once and shared by every seeded test cook: these cooks authenticate via a
# directly-minted token (issue_cook_token), never the password path, so the hash value is irrelevant — we
# just avoid paying bcrypt's deliberate cost per seeded cook.
_TEST_PASSWORD_HASH = security.hash_password("test-password")


class _FakeVault:
    """Minimal `app.state.vault` stand-in: returns the fixed cook signing key for any requested key.

    `require_cook` and the /chat limiter read the COOK_SESSION_KEY off `app.state.vault`; the test app has no
    real Vault, so this returns the one signing key the `auth` fixture also signs with.
    """

    def get(self, _key: str) -> str:
        """Return the single test cook-session signing key regardless of which key name is requested."""
        return _TEST_COOK_SIGNING_KEY

    def ping(self) -> bool:
        """Always reachable in tests (lets the /health route report Vault ok)."""
        return True


class _FakePingable:
    """A reachable adapter stand-in for `app.state.db` so the /health route can report it ok in tests.

    DB sessions come from the overridden `get_db` (the isolated test transaction), so this is consulted
    only by /health's `ping()`; it never serves a session.
    """

    def ping(self) -> bool:
        """Always reachable in tests."""
        return True

# A throwaway, session-unique schema so tests run against an EMPTY namespace — the real `public`
# corpus is invisible (search_path points only here) and untouched (the schema is dropped at the end).
_TEST_SCHEMA = f"test_{uuid.uuid4().hex[:8]}"


def _test_db_url() -> str:
    """Resolve the integration test database URL from the environment (with a localhost default)."""
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("POSTGRES_URL") or _DEFAULT_URL


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Create an isolated test schema + tables, skipping the whole module if no Postgres is reachable.

    A failed initial connect means there is no test database (e.g. CI without the stack), so the
    integration tests skip rather than fail. Every connection's search_path is pinned to the throwaway
    schema, so `create_all` builds the tables there and the production corpus in `public` never leaks
    into a test. The schema is dropped (CASCADE) at session end.
    """
    eng = create_engine(_test_db_url(), pool_pre_ping=True, future=True)

    @event.listens_for(eng, "connect")
    def _pin_search_path(dbapi_conn: object, _record: object) -> None:
        """Pin each connection to the test schema, with public as a fallback for the pgvector type.

        Tables resolve test-schema-first (create_all built them there, shadowing the real public corpus),
        so no corpus leaks; `public` trails only so the `vector` type — installed there by the pgvector
        extension — resolves when the recipes table's embedding column is created/queried.
        """
        cur = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cur.execute(f"SET search_path TO {_TEST_SCHEMA}, public")
        cur.close()

    try:
        conn = eng.connect()
    except Exception:  # noqa: BLE001 — any connection failure means "no test DB", so skip.
        eng.dispose()
        pytest.skip("No test Postgres reachable (set TEST_DATABASE_URL to run integration tests).")
    # Ensure pgvector is available (migration 0001 does this in prod; create_all-based tests need it too),
    # then make the throwaway schema. Both are idempotent.
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_TEST_SCHEMA}"))
    conn.commit()
    conn.close()

    # checkfirst=False forces CREATE in the (empty) test schema: with `public` on the search_path for the
    # pgvector type, create_all's default existence check would otherwise see public.recipes and skip,
    # leaving inserts to fall through to the un-migrated public table. The throwaway schema is fresh, so
    # unconditional creates are safe.
    Base.metadata.create_all(eng, checkfirst=False)
    yield eng
    with eng.connect() as cleanup:
        cleanup.execute(text(f"DROP SCHEMA IF EXISTS {_TEST_SCHEMA} CASCADE"))
        cleanup.commit()
    eng.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    """Yield a session bound to an outer transaction that is rolled back at teardown (full isolation).

    A SAVEPOINT is opened and automatically restarted whenever request-handling code commits, so the
    repo layer's real commit behavior is preserved while the outer transaction still discards everything.
    """
    connection = engine.connect()
    outer = connection.begin()
    session = sessionmaker(bind=connection, expire_on_commit=False)()
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess: Session, trans: object) -> None:
        """Reopen the SAVEPOINT after each inner commit so subsequent requests keep a transaction."""
        if trans.nested and not trans._parent.nested:  # type: ignore[attr-defined]
            sess.begin_nested()

    yield session

    session.close()
    outer.rollback()
    connection.close()


@pytest.fixture
def make_user_client(db_session: Session) -> Callable[..., AsyncClient]:
    """Return a factory for an ASGI client over an app whose get_db yields the isolated test session.

    The app wires only the error handlers and the cook-facing routers — the same registration the real
    factory uses — with get_db overridden so every request shares the test transaction.
    """

    def _override_get_db() -> Iterator[Session]:
        """Yield the test session, committing on success so SAVEPOINT semantics mirror production."""
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    def _factory() -> AsyncClient:
        app = FastAPI()
        register_error_handlers(app)
        register_health_router(app)
        register_user_routers(app)
        # `require_cook`, `/auth/login`, and the /chat limiter read the cook signing key + TTL off app.state;
        # supply a fake Vault (fixed key) and a settings object so the real cook gate runs unchanged. A fake
        # db + settings env/version also let the open /health route answer here (it reads them off app.state).
        app.state.vault = _FakeVault()
        app.state.db = _FakePingable()
        app.state.settings = types.SimpleNamespace(
            cook_jwt_ttl_minutes=_TEST_COOK_TTL_MINUTES, env="test", version="0.0.0-test"
        )
        app.dependency_overrides[get_db] = _override_get_db
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    return _factory


@pytest.fixture
def auth(db_session: Session) -> Callable[..., dict[str, str]]:
    """Return a factory mapping an owner-key string to Bearer auth headers for a real, active cook.

    Seeds (idempotently) a `CookAccount` whose id IS the given owner key, so the value `require_cook`
    resolves and threads downstream is identical to the pre-009 `X-Profile-ID` string — the data-keying in
    these tests is unchanged while the real token gate is exercised. The token is signed with the same key
    the test app's fake Vault hands `require_cook`, so it verifies end to end. Headers are memoized per owner
    so two clients sharing an owner key (e.g. the persistence test) get the same cook id.
    """
    minted: dict[str, dict[str, str]] = {}

    def _auth(owner_id: str = "cook") -> dict[str, str]:
        """Seed the cook for `owner_id` if needed and return its `Authorization: Bearer <jwt>` header."""
        if owner_id not in minted:
            cook = cook_repo.get(db_session, owner_id)
            if cook is None:
                cook = CookAccount(
                    id=owner_id,
                    username=owner_id,
                    display_name=owner_id,
                    is_active=True,
                    password_hash=_TEST_PASSWORD_HASH,
                    created_by=None,
                )
                db_session.add(cook)
                db_session.flush()
            token = security.issue_cook_token(
                cook, signing_key=_TEST_COOK_SIGNING_KEY, ttl_minutes=_TEST_COOK_TTL_MINUTES
            )
            minted[owner_id] = {"Authorization": f"Bearer {token}"}
        return minted[owner_id]

    return _auth
