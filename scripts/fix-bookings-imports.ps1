Write-Host "Phila Backend - add missing imports to bookings.py" -ForegroundColor Cyan

$path = "app\api\routes\bookings.py"
$content = [System.IO.File]::ReadAllText((Resolve-Path $path))

$importBlock = @'

# Phase 4a - walk-in welcome comms
from app.services.walkin_comms import send_walkin_welcome
from app.models.booking_comms_log import BookingCommsLog
'@

# Only add if not already there
if ($content -notmatch "from app\.services\.walkin_comms") {
    # Find the line "from app.api.deps import get_current_user" or any "from app." import block end
    # Safest: insert right before the first "router = APIRouter(...)" line, which exists in every route file
    $anchor = "router = APIRouter("
    if ($content.Contains($anchor)) {
        $content = $content.Replace($anchor, "$importBlock`n`n$anchor")
        [System.IO.File]::WriteAllText((Resolve-Path $path), $content)
        Write-Host "  Inserted imports above router declaration" -ForegroundColor Green
    } else {
        Write-Host "  ERROR: Could not find 'router = APIRouter(' anchor in bookings.py" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  Imports already present - nothing to do" -ForegroundColor Yellow
}

# Local parse check before pushing
Write-Host ""
Write-Host "Parsing app/api/routes/bookings.py..." -ForegroundColor Cyan
python -c "import ast; ast.parse(open('app/api/routes/bookings.py').read()); print('OK - parses cleanly')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Parse failed - do not push" -ForegroundColor Red
    exit 1
}

git add app\api\routes\bookings.py
git commit -m "Add missing Phase 4a imports to bookings.py - send_walkin_welcome and BookingCommsLog. Previous script that built Phase 4a tried to insert these imports but the anchor it used didnt match the actual file structure, so the imports never landed. Walk-in route was using both names without importing them, causing NameError on every walk-in creation. Found via Railway log traceback after manual schema fix didnt resolve the 500s."
git push
Write-Host ""
Write-Host "Pushed. Wait ~2 min for Railway redeploy, then retest walk-in." -ForegroundColor Yellow