import os
import sys
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# Add app to path so we can import Base and settings
sys.path.append(os.getcwd())

# Import your Base for autogenerate support
try:
    from app.db.base import Base
    target_metadata = Base.metadata
except ImportError:
    target_metadata = None

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

def get_url():
    # Priority: Railway environment variable
    url = os.getenv("DATABASE_URL")
    if not url:
        # Fallback for local development
        url = "postgresql://postgres:postgres@localhost:5432/phila"
    
    # Fix for Railway/Heroku postgres prefix requirement
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # We create a custom configuration dictionary to force the correct URL
    connectable = engine_from_config(
        {
            "sqlalchemy.url": get_url(),
        },
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, 
            target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()