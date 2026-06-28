Write-Host "Phila Backend - replace inline CMD with a startup script" -ForegroundColor Cyan

# Create a startup script that does the same thing but with explicit
# exec to replace the shell process with uvicorn. This is the canonical
# Docker pattern - the shell only exists long enough to start the real
# process, then disappears, so signals (SIGTERM from Railway) propagate
# directly to uvicorn for clean shutdowns.
[System.IO.File]::WriteAllText("$PWD\start.sh", @'
#!/bin/sh
set -e

echo "[startup] Running migrations..."
alembic upgrade head

echo "[startup] Starting uvicorn on port ${PORT:-8080}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
'@)
Write-Host "  Created start.sh" -ForegroundColor Green

# Rewrite Dockerfile to use the script
[System.IO.File]::WriteAllText("$PWD\Dockerfile", @'
FROM python:3.12-slim

# WeasyPrint runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libgobject-2.0-0 \
    libglib2.0-0 \
    libgdk-pixbuf-2.0-0 \
    shared-mime-info \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pip deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Make startup script executable and run it. exec form ensures
# Railway signals propagate properly to uvicorn for clean shutdowns.
RUN chmod +x /app/start.sh
CMD ["/app/start.sh"]
'@)
Write-Host "  Rewrote Dockerfile to use start.sh entrypoint" -ForegroundColor Green

git add Dockerfile start.sh
git commit -m "Replace inline shell CMD with start.sh entrypoint. Previous CMD with '&& uvicorn' was silently failing - alembic completed (visible in logs) but uvicorn never started (no Application startup complete, no Uvicorn running message, Railway proxy returns Application failed to respond). Suspect was that the && chain broke when alembic detached its stdio. The canonical fix is a startup script with exec - shell launches uvicorn directly via exec, replacing itself with the uvicorn process. Signal propagation works correctly, port binding behaves predictably."
git push
Write-Host ""
Write-Host "Pushed. Watch the deploy log - expect to see [startup] Running migrations / [startup] Starting uvicorn lines, then INFO: Application startup complete." -ForegroundColor Yellow