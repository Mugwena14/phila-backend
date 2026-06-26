Write-Host "Phila Backend - Phase 4a syntax fix - remove stray 'return' from response assignment" -ForegroundColor Cyan

$path = "app\api\routes\bookings.py"
$content = [System.IO.File]::ReadAllText((Resolve-Path $path))

$broken = "response = return BookingDetailResponse("
$fixed  = "response = BookingDetailResponse("

if ($content.Contains($broken)) {
    $content = $content.Replace($broken, $fixed)
    [System.IO.File]::WriteAllText((Resolve-Path $path), $content)
    Write-Host "  Fixed stray 'return' in response assignment" -ForegroundColor Green
} else {
    Write-Host "  Anchor not found - inspect line 254 of bookings.py manually" -ForegroundColor Yellow
    exit 1
}

# Sanity check - boot the app locally to confirm it parses
Write-Host ""
Write-Host "Quick syntax check..." -ForegroundColor Cyan
python -c "import ast; ast.parse(open('app/api/routes/bookings.py').read()); print('OK - parses cleanly')"

git add app\api\routes\bookings.py
git commit -m "Fix Phase 4a syntax error - bookings.py had 'response = return BookingDetailResponse(...)' which is not valid Python. Was caused by the script that extended create_walk_in_booking using regex to capture the existing return statement, then prepending 'response = ' to the captured string without stripping the 'return' keyword. Migration applied fine on the previous push; this is a code-only fix, no migration needed."
Write-Host ""
Write-Host "Fix committed. Push to redeploy." -ForegroundColor Yellow