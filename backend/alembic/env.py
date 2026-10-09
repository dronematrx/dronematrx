from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.base import Base
from app.models import *  # noqa: F401,F403  -- register all models on Base.metadata

config = context.config
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# PostGIS owns spatial_ref_sys; it is not one of our models and must never be dropped.
POSTGIS_SYSTEM_TABLES = {"spatial_ref_sys"}
# Indexes created by hand in migrations (TimescaleDB hypertable / DESC ordering).
MANUAL_INDEXES = {"ix_drone_telemetry_drone_time", "drone_telemetry_timestamp_idx"}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "index" and name in MANUAL_INDEXES:
        return False
    return not (type_ == "table" and name in POSTGIS_SYSTEM_TABLES)


def run_migrations_offline() -> None:
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
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
