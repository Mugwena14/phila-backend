Write-Host "Phila Backend - switch from nixpacks to Dockerfile" -ForegroundColor Cyan

# 1. Create the Dockerfile
[System.IO.File]::WriteAllText("$PWD\Dockerfile", @'
FROM python:3.12-slim

# WeasyPrint runtime dependencies - the entire reason we are switching
# from nixpacks. nixpacks aptPkgs was silently not installing these and
# burned hours of debugging. Dockerfile gives us a clear contract.
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

# Cache pip install separately from app code - rebuilds are faster
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code last so changes here dont invalidate the pip layer
COPY . .

# Alembic upgrade head before uvicorn starts. If migrations fail, the
# container fails to start and Railway shows it as a deploy failure -
# exactly the loud failure mode we want.
CMD ["bash", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
'@)
Write-Host "  Created Dockerfile" -ForegroundColor Green

# 2. Delete nixpacks.toml - Railway prioritises Dockerfile over nixpacks
#    when both exist, but cleaner to remove the dead config entirely.
if (Test-Path nixpacks.toml) {
    Remove-Item nixpacks.toml
    Write-Host "  Removed nixpacks.toml" -ForegroundColor Green
}

# 3. Update railway.toml - preDeployCommand was running alembic, now the
#    Dockerfile CMD does that. Keep railway.toml minimal so theres one
#    source of truth.
[System.IO.File]::WriteAllText("$PWD\railway.toml", @'
# Railway build is now handled by Dockerfile. Migrations run as part of
# the container CMD - if they fail, the container fails to start, which
# Railway surfaces as a deploy failure (correct behaviour).

[build]
builder = "DOCKERFILE"
'@)
Write-Host "  Updated railway.toml to use DOCKERFILE builder" -ForegroundColor Green

git add Dockerfile railway.toml
if (Test-Path nixpacks.toml) {
    git add nixpacks.toml
} else {
    # File was deleted, stage the deletion
    git rm nixpacks.toml -q 2>$null
}

git commit -m "Switch backend build from nixpacks to Dockerfile. nixpacks aptPkgs block was silently not installing libgobject/libpango despite being listed - WeasyPrint kept falling back to HTML output, which Twilio rejected as unsupported WhatsApp media (error 63019 reported as 'media failed to download', misleading). Dockerfile gives a clear contract: we control the base image, apt installs are explicit, runtime libs match exactly what WeasyPrint imports. railway.toml simplified - the alembic upgrade now runs as part of the Dockerfile CMD instead of preDeployCommand, single source of truth for container lifecycle. Cost: ~1 minute slower cold builds. Benefit: reproducible runtime."

Write-Host ""
Write-Host "Committed. Push to deploy. Watch the build log for apt-get install lines listing all 8 packages." -ForegroundColor Yellow