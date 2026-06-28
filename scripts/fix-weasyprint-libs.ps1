Write-Host "Phila Backend - add missing WeasyPrint system deps to nixpacks.toml" -ForegroundColor Cyan

[System.IO.File]::WriteAllText("$PWD\nixpacks.toml", @'
# WeasyPrint runtime dependencies for Railways Debian-based nixpacks image.
[phases.setup]
aptPkgs = [
  "libpango-1.0-0",
  "libpangoft2-1.0-0",
  "libcairo2",
  "libffi-dev",
  "libgobject-2.0-0",
  "libglib2.0-0",
  "libgdk-pixbuf-2.0-0",
  "shared-mime-info",
]
'@)

git add nixpacks.toml
git commit -m "Add missing WeasyPrint runtime deps to nixpacks.toml. libgobject and libglib are required by Pango at runtime; Railways nixpacks image was leaving them out, causing WeasyPrint to silently fall back to HTML output. That HTML was then being served to Twilio for WhatsApp media which rejected it as unsupported format - misreported as error 63019 'Media failed to download'. Real root cause was format mismatch, not a transport failure."
git push
Write-Host "Pushed. Railway redeploy in ~3 minutes. After that, doc sends produce real PDFs and WhatsApp accepts them." -ForegroundColor Yellow