# Custom PostgreSQL + PostGIS image built locally on top of the official
# postgres:16 image. We build this instead of pulling postgis/postgis
# directly because the public postgis/postgis image layers were found to
# extract as zero-byte binaries on some Docker Desktop hosts; postgres:16
# already ships the matching apt.postgresql.org (PGDG) repo, so we can just
# apt-get install the postgis packages for PG16 on top of a known-good base.
FROM postgres:16

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-16-postgis-3 \
        postgresql-16-postgis-3-scripts \
    && rm -rf /var/lib/apt/lists/*
