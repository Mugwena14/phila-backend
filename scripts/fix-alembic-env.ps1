Write-Host "Phila Backend - Fix alembic env.py DATABASE_URL loading" -ForegroundColor Cyan

Set-Content "alembic/env.py" @'
import os
import sys
from logging.config import fileConfig
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool
from alembic import context

load_dotenv()

# config object provides access to alembic.ini
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 1. Manually define metadata or import it carefully
# If imports fail, we can still run migrations manually
target_metadata = None
try:
    sys.path.append(os.getcwd())
    from app.db.base import Base
    target_metadata = Base.metadata
except Exception as e:
    print(f"[alembic env.py] Could not import Base for autogenerate: {e}")

def get_url():
    url = os.environ.get("DATABASE_URL")

    if not url:
        url = config.get_main_option("sqlalchemy.url")

    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    return url

def run_migrations_online() -> None:
    db_url = get_url()
    print(f"Connecting to database... (URL detected: {db_url[:15]}...)")

    connectable = engine_from_config(
        {"sqlalchemy.url": db_url},
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
'@
Write-Host "  Updated alembic/env.py - now loads .env directly, exception no longer silently swallowed" -ForegroundColor Green

git add .
git commit -m "Fix alembic env.py - explicitly load .env, stop silently swallowing the Base import exception"
Write-Host "Done and committed!" -ForegroundColor Green