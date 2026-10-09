-- Runs automatically on first container start (fresh volume only), via
-- postgres's docker-entrypoint-initdb.d convention. Existing volumes get
-- timescaledb from the Alembic migration instead.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS timescaledb;
