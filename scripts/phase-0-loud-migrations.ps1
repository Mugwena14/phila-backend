Write-Host "Phila Backend - make Alembic loud about what it actually does, remove Procfile duplicate" -ForegroundColor Cyan

Set-Content "alembic/env.py" @'
import os
import sys
from logging.config import fileConfig
from urllib.parse import urlparse
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool, text
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

def _read_current_revision(connection) -> str:
    try:
        result = connection.execute(text("SELECT version_num FROM alembic_version"))
        row = result.fetchone()
        return row[0] if row else "<empty>"
    except Exception:
        return "<no alembic_version table yet>"

def run_migrations_online() -> None:
    db_url = get_url()
    host = "<unknown>"
    try:
        host = urlparse(db_url).hostname or "<unknown>"
    except Exception:
        pass

    print(f"[alembic] Starting upgrade against host: {host}")

    connectable = engine_from_config(
        {"sqlalchemy.url": db_url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        before_rev = _read_current_revision(connection)
        print(f"[alembic] Revision before upgrade: {before_rev}")

        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()

        after_rev = _read_current_revision(connection)
        print(f"[alembic] Revision after upgrade:  {after_rev}")

        if before_rev == after_rev:
            print(f"[alembic] No migrations applied - database already at head ({after_rev})")
        else:
            print(f"[alembic] Migrations applied: {before_rev} -> {after_rev}")

    print("[alembic] Upgrade step complete")

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
Write-Host "  Updated alembic/env.py - now logs host, before/after revision, and whether anything was applied" -ForegroundColor Green

if (Test-Path "Procfile") {
    Remove-Item "Procfile"
    Write-Host "  Removed Procfile - railway.toml is now the sole source of truth for the start command" -ForegroundColor Green
} else {
    Write-Host "  Procfile not found - already removed or never existed" -ForegroundColor Yellow
}

git add .
git commit -m "Phase 0 - make Alembic migrations loud on deploy, remove Procfile duplicate. env.py now logs DB host, revision before, revision after, and whether anything was applied. Procfile deleted so railway.toml is single source of truth for startCommand"
Write-Host "Committed locally. Push to deploy and watch the next deploy log carefully" -ForegroundColor Yellow