"""Alembic environment for VeriClaim's reviewed database schema."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.database import Base
from app.models import domain as _domain_models  # noqa: F401 - registers metadata

# Alembic's Config object provides values from alembic.ini and command options.
config = context.config

# Runtime configuration always wins over the example/default URL in alembic.ini.
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Includes the compatibility AuditRecord and every architecture-foundation model.
target_metadata = Base.metadata


def _configure_context(**kwargs: object) -> None:
    """Centralize safe autogeneration defaults for PostgreSQL and SQLite tests."""

    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=settings.database_url.startswith("sqlite"),
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Run migrations without opening a database connection."""

    _configure_context(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the configured database."""

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        _configure_context(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
