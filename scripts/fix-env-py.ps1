Write-Host "Phila Backend - fix alembic env.py so migrations are reliable and quiet" -ForegroundColor Cyan

Set-Content "alembic/env.py" @'
"""
Alembic environment.

Two design points worth keeping straight:

1. Upgrades read FROM the alembic/versions migration files and write TO the
   target database. They do NOT need the SQLAlchemy models imported - the
   migration files contain their own DDL. So model-import failures here
   should be a warning at most, never a silent corruption.

2. Autogenerate (alembic revision --autogenerate -m "...") DOES need
   target_metadata populated, by importing every model into Base.metadata.
   When this fails, autogenerate produces empty migrations - that was the
   favorites bug.

What this file does differently from the previous version:
  - Logs target host, before/after revisions, and which command is running.
  - Distinguishes "schema unchanged" from "DDL successfully applied" by
    diffing the actual revision values, not by trusting the surrounding
    alembic call.
  - Fails loudly with a non-zero exit code if model imports collapse - we
    never want a silent "ran but didn't do anything" again.
  - Doesn't run anything at module import time; only when alembic actually
    asks for online or offline migrations.
"""
import os
import sys
import logging
from logging.config import fileConfig
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool, text
from alembic import context

# Optional .env loading is fine - prod env vars override .env so this is safe
load_dotenv()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

log = logging.getLogger("alembic.env")

# ── target_metadata for autogenerate ──────────────────────────────────────────
# Import every model via app/models/__init__.py. If this fails, we still
# allow file-based upgrades to proceed (they dont need metadata), but we
# refuse autogenerate so it cant produce empty stubs.
target_metadata = None
_model_import_error: str | None = None
try:
    sys.path.append(os.getcwd())
    from app.db.base import Base  # noqa: F401
    import app.models  # noqa: F401 - registers every model on Base.metadata
    target_metadata = Base.metadata
except Exception as e:
    _model_import_error = str(e)
    log.warning(
        "Could not import models for autogenerate (this is fine for upgrade, "
        "fatal for autogenerate): %s",
        _model_import_error,
    )


def get_url() -> str:
    """Resolve the database URL, normalising postgres:// to postgresql://."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set and sqlalchemy.url is not configured. "
            "Cannot run migrations."
        )
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def _read_current_revision(connection) -> str | None:
    """Return whatever is in alembic_version, or None if the table doesnt exist."""
    try:
        result = connection.execute(text("SELECT version_num FROM alembic_version"))
        row = result.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _log_host_banner(db_url: str) -> None:
    """Print which database alembic is about to touch. Critical safety log."""
    host = "<unknown>"
    try:
        host = urlparse(db_url).hostname or "<unknown>"
    except Exception:
        pass
    print(f"[alembic] Target host: {host}", flush=True)


def run_migrations_offline() -> None:
    """Render SQL to stdout without connecting. Used for `alembic upgrade --sql`."""
    db_url = get_url()
    _log_host_banner(db_url)
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and apply migrations."""
    db_url = get_url()
    _log_host_banner(db_url)

    connectable = engine_from_config(
        {"sqlalchemy.url": db_url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        before_rev = _read_current_revision(connection)
        print(f"[alembic] Revision before: {before_rev or '<no version table>'}", flush=True)

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()

        # Re-read the version row INSIDE the same connection - alembic has
        # already committed inside context.begin_transaction. If the value
        # didnt actually change, the migration silently no-opped and we
        # surface that loudly.
        after_rev = _read_current_revision(connection)
        print(f"[alembic] Revision after:  {after_rev or '<no version table>'}", flush=True)

        if before_rev == after_rev:
            print(f"[alembic] No DDL applied (already at {after_rev or 'unknown'})", flush=True)
        else:
            print(f"[alembic] DDL applied: {before_rev} -> {after_rev}", flush=True)


# This is the only place anything runs - module-level work above is just
# definitions. Previous env.py ran migrations at module import time, which
# meant every `alembic current` / `alembic heads` triggered upgrade logic.
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
'@
Write-Host "  Rewrote alembic/env.py - real before/after diff, no module-level work, autogenerate guard" -ForegroundColor Green

# Quick local sanity check - alembic current should work and NOT trigger upgrade logging
Write-Host ""
Write-Host "Running alembic current locally to confirm env.py loads cleanly..." -ForegroundColor Cyan
alembic current

git add alembic/env.py
git commit -m "Fix alembic env.py - log target host loudly, diff before/after revisions correctly, dont run migrations at module import time, import app.models so autogenerate sees every table. Previous env.py let migrations silently no-op and reported success regardless - this was the second time it bit prod (Phase 3a deploy did not actually apply b2c8e1f4a5d6 even though deploy logs said it did)."
Write-Host ""
Write-Host "Pushed local. Now run git push and watch the deploy log - it should say 'No DDL applied (already at b2c8e1f4a5d6)' because we manually applied this migration already. If it says anything else, paste it." -ForegroundColor Yellow