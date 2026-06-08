-- Postgres init script: create a dedicated schema for Phoenix's trace store so its Alembic
-- migrations live separately from the app's (whose alembic_version sits in `public`). Runs once,
-- automatically, on first database init via /docker-entrypoint-initdb.d. Idempotent.
CREATE SCHEMA IF NOT EXISTS phoenix;
