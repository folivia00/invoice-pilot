"""Alembic environment: synchronous SQLite (stdlib driver), batch mode for ALTERs."""

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

from invoice_pilot.config import DEFAULT_DATA_DIR
from invoice_pilot.storage.database import sqlite_url
from invoice_pilot.storage.tables import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    data_dir = Path(os.environ.get("DATA_DIR", "").strip() or DEFAULT_DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    return sqlite_url(data_dir / "invoice_pilot.db", driver="")


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
