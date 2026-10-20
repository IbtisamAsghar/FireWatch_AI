"""Online/offline migrations, including explicitly supplied test connections."""

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from firewatch.config import get_settings
from firewatch.db import models  # noqa: F401 -- register all tables
from firewatch.db.base import Base

config = context.config


def migrate(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url="postgresql+psycopg://",
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    migrate(config.attributes["connection"])
else:
    engine = create_engine(
        get_settings().database_url.get_secret_value(),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    try:
        with engine.connect() as connection:
            migrate(connection)
    finally:
        engine.dispose()
