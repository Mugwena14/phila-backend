Write-Host "Manually applying Phase 3a migration to prod" -ForegroundColor Cyan
Write-Host "This is the same pattern we used for the favorites recovery" -ForegroundColor Yellow

# 1. Get the public Railway Postgres URL
Write-Host ""
Write-Host "STEP 1: Open Railway dashboard -> Postgres service -> Connect tab" -ForegroundColor Cyan
Write-Host "Copy the 'Public Network' connection URL (the one with tramway.proxy.rlwy.net hostname)" -ForegroundColor Cyan
Write-Host "Paste it below when prompted." -ForegroundColor Cyan
Write-Host ""
$publicUrl = Read-Host "Paste the Railway public Postgres URL"

if (-not $publicUrl -or -not $publicUrl.StartsWith("postgresql://")) {
    Write-Host "That doesn't look like a valid postgres URL. Aborting." -ForegroundColor Red
    exit 1
}

# 2. Rename .env so load_dotenv() doesn't override $env:DATABASE_URL
#    Same dance we did during the favorites recovery
if (Test-Path .env) {
    Rename-Item .env .env.local-backup
    Write-Host "  Renamed .env to .env.local-backup (will restore at end)" -ForegroundColor Green
}

try {
    # 3. Point alembic at prod
    $env:DATABASE_URL = $publicUrl

    Write-Host ""
    Write-Host "Current prod revision before upgrade:" -ForegroundColor Cyan
    alembic current

    Write-Host ""
    Write-Host "Running alembic upgrade head against prod..." -ForegroundColor Cyan
    alembic upgrade head

    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Alembic returned a non-zero exit code. Read the output above carefully." -ForegroundColor Red
        Write-Host "The migration may have partially applied. Do NOT push more code until this is sorted." -ForegroundColor Red
    } else {
        Write-Host ""
        Write-Host "Revision after upgrade:" -ForegroundColor Cyan
        alembic current

        Write-Host ""
        Write-Host "Verifying the new columns exist on patient_documents..." -ForegroundColor Cyan
        python -c "import os; from sqlalchemy import create_engine, text; e = create_engine(os.environ['DATABASE_URL']); cols = [r[0] for r in e.connect().execute(text(\"SELECT column_name FROM information_schema.columns WHERE table_name='patient_documents' ORDER BY column_name\"))]; print('patient_documents columns:'); [print(' ', c) for c in cols]; missing = [c for c in ['sent_via_whatsapp_at', 'sent_via_email_at', 'recalled_at'] if c not in cols]; print(); print('MISSING:', missing) if missing else print('All Phase 3a columns present.')"
    }
} finally {
    # 4. Always restore .env, even if alembic blew up
    if (Test-Path .env.local-backup) {
        Rename-Item .env.local-backup .env
        Write-Host ""
        Write-Host "  Restored .env" -ForegroundColor Green
    }

    # 5. Clear the DATABASE_URL env var so subsequent commands don't accidentally hit prod
    Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
    Write-Host "  Cleared `$env:DATABASE_URL" -ForegroundColor Green
}

Write-Host ""
Write-Host "Done. The 500s should stop. Hit /api/v1/documents/patient/{id} once to confirm." -ForegroundColor Yellow