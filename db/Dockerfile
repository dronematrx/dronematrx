# Custom PostgreSQL + PostGIS + TimescaleDB image built locally on top of the
# official postgres:16 image. We build this instead of pulling postgis/postgis
# directly because the public postgis/postgis image layers were found to
# extract as zero-byte binaries on some Docker Desktop hosts; postgres:16
# already ships the matching apt.postgresql.org (PGDG) repo, so we can just
# apt-get install the postgis and timescaledb packages for PG16 on top of a
# known-good base.
#
# Sprint 4: TimescaleDB is an extension of this same database (telemetry
# hypertable), not a second database. It must be preloaded -- see the
# `shared_preload_libraries` command flag in docker-compose.yml.
FROM postgres:16

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-16-postgis-3 \
        postgresql-16-postgis-3-scripts \
        postgresql-16-timescaledb \
    && rm -rf /var/lib/apt/lists/*
