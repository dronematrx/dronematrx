-- Runs automatically on first container start (fresh volume only), via
-- postgres's docker-entrypoint-initdb.d convention.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
