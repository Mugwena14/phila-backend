$dockerfile = [System.IO.File]::ReadAllText("$PWD\Dockerfile")

$old = 'CMD ["bash", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]'
$new = 'CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]'

if ($dockerfile.Contains($old)) {
    $dockerfile = $dockerfile.Replace($old, $new)
    [System.IO.File]::WriteAllText("$PWD\Dockerfile", $dockerfile)
    Write-Host "  Changed CMD to use sh instead of bash" -ForegroundColor Green

    git add Dockerfile
    git commit -m "Fix Dockerfile - use sh instead of bash. python:3.12-slim ships only sh by default; the bash -c CMD was silently failing because bash isnt in the image. Alembic was running because it doesnt need a shell, but uvicorn never launched because the && chain depends on a working shell."
    git push
    Write-Host "Pushed. Watch the deploy log - this time uvicorn should actually start." -ForegroundColor Yellow
} else {
    Write-Host "  CMD line not found in expected form - check Dockerfile manually" -ForegroundColor Red
}