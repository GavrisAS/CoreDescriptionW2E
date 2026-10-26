param(
    [string]$Commit = "",
    [ValidatePattern('^0\.1\.(0|[1-9][0-9]*)(\+local)?$')][string]$Version = "0.1.0+local"
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (-not $IsWindows) { throw "Windows packaging requires Windows" }
Set-Location (Split-Path $PSScriptRoot -Parent)
function Run-UV {
    & uv @args
    if ($LASTEXITCODE -ne 0) { throw "uv command failed: $args" }
}
Run-UV sync --locked
Run-UV run ruff check .
Run-UV run ruff format --check .
Run-UV run ty check
Run-UV run pytest
if (-not $Commit) { $Commit = (& git rev-parse HEAD).Trim() }
$versionModule = "src/coredescriptionw2e/_build_version.py"
if (Test-Path $versionModule) { throw "Build version module already exists" }
try {
    "VERSION = `"$Version`"" | Set-Content $versionModule -Encoding utf8
    Run-UV run pyinstaller --noconfirm --clean --onedir --windowed --name CoreDescriptionW2E --paths src --add-data "tests/fixtures/synthetic.doc;fixtures" src/coredescriptionw2e/gui/app.py
} finally {
    Remove-Item $versionModule -ErrorAction SilentlyContinue
}
$app = Join-Path (Get-Location) "dist/CoreDescriptionW2E"
# Only Qt modules covered by the exact qtbase/qtsvg source archives are allowed.
$allowedQt = @("Qt6Core", "Qt6Gui", "Qt6Widgets", "Qt6Network", "Qt6DBus", "Qt6OpenGL", "Qt6Svg", "Qt6SvgWidgets", "Qt6PrintSupport", "Qt6Xml")
Get-ChildItem $app -Recurse -Filter "Qt6*.dll" | ForEach-Object {
    if ($_.BaseName -notin $allowedQt) { throw "Unaudited Qt component: $($_.Name)" }
}
Run-UV run python scripts/package_licenses.py --destination $app
$manifest = @{version=$Version; commit=$Commit; python=(& uv run python --version); doc_backend="ms-doc-piece-table+olefile-0.47"; sources_included=$true}
$manifest | ConvertTo-Json | Set-Content (Join-Path $app "manifest.json") -Encoding utf8
$archive = Join-Path (Get-Location) "dist/CoreDescriptionW2E-v$Version-windows-x64.zip"
if (Test-Path $archive) { throw "Archive exists; use a fresh build directory" }
Compress-Archive -Path $app -DestinationPath $archive
$result = & "$PSScriptRoot/verify_windows.ps1" -Archive $archive -Version $Version
$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
"$hash  $(Split-Path $archive -Leaf)" | Set-Content "$archive.sha256" -Encoding ascii
$manifest.archive = Split-Path $archive -Leaf
$manifest.sha256 = $hash
$manifest.exe_sha256 = (Get-FileHash (Join-Path $app "CoreDescriptionW2E.exe") -Algorithm SHA256).Hash.ToLower()
$manifest.smoke = $result
$manifest | ConvertTo-Json -Depth 5 | Set-Content "dist/manifest.json" -Encoding utf8
Run-UV run python scripts/release.py verify --folder dist --version $Version --commit $Commit
Write-Output "Portable ZIP and frozen DOC/DOCX + GUI acceptance: PASS"
