# Eden.tools Downloader - bauen und als GitHub-Release veroeffentlichen.
#
#   .\veroeffentlichen.ps1            baut die EXE und veroeffentlicht sie als Release v<APP_VERSION>
#   .\veroeffentlichen.ps1 -NurBauen  baut nur (dist\Eden-Downloader.exe)
#
# Vorher: APP_VERSION in eden_downloader.py hochzaehlen und in CHANGELOG.md einen Abschnitt
# "## <version>" schreiben - daraus werden die Notizen, die die App beim Update anzeigt.
# Alle installierten Apps sehen das Release beim naechsten Start.
param([switch]$NurBauen)
# "Continue": Windows PowerShell 5.1 haelt sonst jede stderr-Zeile von pip/PyInstaller fuer einen Fehler.
# Fehlschlaege werden unten ueber $LASTEXITCODE abgefangen.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

$quelle = Get-Content -Raw -Encoding UTF8 "eden_downloader.py"
if ($quelle -notmatch 'APP_VERSION = "([\d.]+)"') { throw "APP_VERSION nicht gefunden" }
$version = $Matches[1]
$tag = "v$version"
Write-Host "Eden Downloader $version" -ForegroundColor Cyan

if (-not $NurBauen) {
    $null = gh release view $tag --repo marvEDEN/eden-downloader 2>$null
    if ($LASTEXITCODE -eq 0) { throw "Release $tag gibt es schon - erst APP_VERSION hochzaehlen." }
    $log = Get-Content -Raw -Encoding UTF8 "CHANGELOG.md"
    $m = [regex]::Match($log, "(?ms)^## $([regex]::Escape($version))\s*\r?\n(.*?)(?=^## |\z)")
    if (-not $m.Success -or -not $m.Groups[1].Value.Trim()) { throw "In CHANGELOG.md fehlt der Abschnitt '## $version'." }
    $notizen = $m.Groups[1].Value.Trim()
}

# --- Bauen -------------------------------------------------------------------
Write-Host "[1/3] Pakete" -ForegroundColor Cyan
py -3.12 -m pip install --quiet --upgrade pyinstaller pywebview "yt-dlp[default]" imageio-ffmpeg
if ($LASTEXITCODE) { throw "pip fehlgeschlagen" }

# YouTube braucht Deno. WinGet legt unter Links nur einen Verweis ab - die echte Datei nehmen.
$deno = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\DenoLand.Deno*\deno.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $deno) { throw "deno.exe nicht gefunden (winget install DenoLand.Deno)" }

Write-Host "[2/3] EXE bauen" -ForegroundColor Cyan
py -3.12 -m PyInstaller --noconfirm --clean --onefile --windowed --log-level WARN `
  --name "Eden Downloader" `
  --add-data "web;web" `
  --add-binary "$($deno.FullName);bin" `
  --collect-all webview `
  --collect-all yt_dlp `
  --collect-all yt_dlp_ejs `
  --collect-all imageio_ffmpeg `
  "eden_downloader.py"
if ($LASTEXITCODE) { throw "PyInstaller fehlgeschlagen" }

$exe = "dist\Eden-Downloader.exe"
Copy-Item "dist\Eden Downloader.exe" $exe -Force
$sha = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$exe.sha256" -Value "$sha  Eden-Downloader.exe" -Encoding Ascii -NoNewline
Write-Host ("      {0:N0} MB  sha256 {1}" -f ((Get-Item $exe).Length / 1MB), $sha)

if ($NurBauen) { Write-Host "Fertig: $exe" -ForegroundColor Green; exit 0 }

# --- Veroeffentlichen --------------------------------------------------------
Write-Host "[3/3] Release $tag" -ForegroundColor Cyan
$nf = New-TemporaryFile
Set-Content -Path $nf -Value $notizen -Encoding UTF8
gh release create $tag $exe "$exe.sha256" --repo marvEDEN/eden-downloader --title "Eden Downloader $version" --notes-file $nf
$rc = $LASTEXITCODE
Remove-Item $nf
if ($rc) { throw "gh release create fehlgeschlagen" }
Write-Host "Veroeffentlicht. Die Apps holen $version beim naechsten Start." -ForegroundColor Green
