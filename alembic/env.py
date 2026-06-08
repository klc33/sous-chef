"""Alembic environment: migrations run against app.models metadata.

The DB URL is sourced at runtime from app settings (which read the environment / Vault), not
hardcoded in alembic.ini, so the same migrations work locally and on Railway.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from app.config import get_settings
from app.models import metadata as target_metadata
from sqlalchemy import engine_from_config, pool

# Alembic Config object providing access to alembic.ini values.
config = context.config

# Configure Python logging from the ini file, if present.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the runtime DB URL from settings (env), overriding alembic.ini's empty placeholder.
config.set_main_option("sqlalchemy.url", get_settings().postgres_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL using only the URL, no live connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live connection from an engine built off the settings URL."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
