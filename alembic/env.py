"""Alembic environment configuration.

Loads DATABASE_URL from .env and imports the project's SQLAlchemy Base
so that `--autogenerate` can diff models against the live database.
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context
from dotenv import load_dotenv

# Ensure project root is on sys.path so we can import database.py etc.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Load environment variables from .env
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

# Import the project's Base metadata (all models register on Base)
from database import Base  # noqa: E402

# Alembic Config object
config = context.config

# Override sqlalchemy.url from the environment variable
# Escape '%' for configparser interpolation (% -> %%)
db_url = os.environ["DATABASE_URL"].replace("%", "%%")
config.set_main_option("sqlalchemy.url", db_url)

# Set up Python logging from the ini file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Point Alembic at our metadata for autogenerate support
target_metadata = Base.metadata

# Legacy indexes created by hand-written migration scripts before Alembic was
# adopted.  They duplicate the auto-generated ix_* indexes from column-level
# index=True declarations.  We exclude them so autogenerate doesn't try to drop
# them on every diff.
_LEGACY_INDEXES = frozenset({
    "idx_photo_debt_created_at",
    "idx_photo_debt_due_at",
    "idx_photo_debt_tool_id",
    "idx_photo_debt_tool_name",
    "idx_photo_debt_user_id",
    "idx_photo_uploaded",
})


def _include_object(obj, name, type_, reflected, compare_to):
    """Filter out legacy indexes from autogenerate comparison."""
    if type_ == "index" and name in _LEGACY_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to DB)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,       # detect column type changes
            compare_server_default=True,  # detect default changes
            include_object=_include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
