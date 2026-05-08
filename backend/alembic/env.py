from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool, text
from alembic import context
from app.config import settings
from app.database import Base
# Import all models so autogenerate sees them:
from app import models  # noqa

config = context.config

# Prefer MIGRATION_DATABASE_URL (session pooler) so advisory locks work.
migration_url = settings.migration_database_url or settings.database_url
config.set_main_option("sqlalchemy.url", migration_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Supabase applies a default statement_timeout on the postgres role.
        # Schema changes against a non-empty calls table can exceed it; disable
        # both timeouts for the migration session.
        #
        # SQLAlchemy 2.x autobegins a transaction on the first execute(), so
        # we commit before handing the connection to alembic. Otherwise
        # alembic's `with context.begin_transaction()` opens a SAVEPOINT
        # inside the autobegun txn, releases the SAVEPOINT cleanly on exit,
        # and the outer transaction never commits — every migration appears
        # to run successfully but rolls back when the connection closes.
        connection.execute(text("SET statement_timeout = 0"))
        connection.execute(text("SET lock_timeout = 0"))
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
