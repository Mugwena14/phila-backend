import os
import sys
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# config object provides access to alembic.ini
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 1. Manually define metadata or import it carefully
# If imports fail, we can still run migrations manually
target_metadata = None 
try:
    # Attempt to get your models so autogenerate works
    sys.path.append(os.getcwd())
    from app.db.base import Base
    target_metadata = Base.metadata
except Exception:
    pass

def get_url():
    # Priority 1: The real Railway Variable
    url = os.environ.get("DATABASE_URL")
    
    # Priority 2: If we are local, it might be in alembic.ini
    if not url:
        url = config.get_main_option("sqlalchemy.url")

    # Fix the 'postgres://' vs 'postgresql://' issue
    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    
    return url

def run_migrations_online() -> None:
    db_url = get_url()
    print(f"Connecting to database... (URL detected: {db_url[:15]}...)")

    connectable = engine_from_config(
        {"sqlalchemy.url": db_url}, # This forces the URL
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
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations_online()