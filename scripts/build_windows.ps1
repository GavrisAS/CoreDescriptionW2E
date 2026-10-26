param([string]$Commit = "local")
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
Run-UV run pyinstaller --noconfirm --clean --onedir --windowed --name CoreDescriptionW2E --paths src --add-data "tests/fixtures/synthetic.doc;fixtures" src/coredescriptionw2e/gui/app.py
$app = Join-Path (Get-Location) "dist/CoreDescriptionW2E"
# Only Qt modules covered by the exact qtbase/qtsvg source archives are allowed.
$allowedQt = @("Qt6Core", "Qt6Gui", "Qt6Widgets", "Qt6Network", "Qt6DBus", "Qt6OpenGL", "Qt6Svg", "Qt6SvgWidgets", "Qt6PrintSupport", "Qt6Xml")
Get-ChildItem $app -Recurse -Filter "Qt6*.dll" | ForEach-Object {
    if ($_.BaseName -notin $allowedQt) { throw "Unaudited Qt component: $($_.Name)" }
}
Run-UV run python scripts/package_licenses.py --destination $app
$version = (& uv run python -c 'from coredescriptionw2e import __version__; print(__version__)').Trim()
if ($LASTEXITCODE -ne 0) { throw "Cannot read version" }
$manifest = @{version=$version; commit=$Commit; python=(& uv run python --version); doc_backend="ms-doc-piece-table+olefile-0.47"; sources_included=$true}
$manifest | ConvertTo-Json | Set-Content (Join-Path $app "manifest.json") -Encoding utf8
$archive = Join-Path (Get-Location) "dist/CoreDescriptionW2E-v$version-windows-x64.zip"
Compress-Archive -Path $app -DestinationPath $archive -Force
# Acceptance runs ONLY from the newly unpacked portable, with a minimal PATH.
$clean = Join-Path $env:RUNNER_TEMP "Portable проверка с пробелами"
if (Test-Path $clean) { Remove-Item $clean -Recurse -Force }
Expand-Archive -Path $archive -DestinationPath $clean
$exe = Join-Path $clean "CoreDescriptionW2E/CoreDescriptionW2E.exe"
$previousPath = $env:PATH
$previousCwd = Get-Location
try {
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    Set-Location $clean
    foreach ($arguments in @(@("--smoke-test"), @("--frozen-smoke", "acceptance"))) {
        $process = Start-Process -FilePath $exe -ArgumentList $arguments -PassThru
        if (-not $process.WaitForExit(90000)) { $process.Kill(); throw "Frozen smoke timed out" }
        if ($process.ExitCode -ne 0) { throw "Frozen smoke failed: $($process.ExitCode)" }
    }
    $result = Get-Content "acceptance/smoke-result.json" -Raw | ConvertFrom-Json
    if ($result.status -ne "PASS" -or -not $result.frozen) { throw "Frozen acceptance did not pass" }
} finally {
    Set-Location $previousCwd
    $env:PATH = $previousPath
}
$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
"$hash  $(Split-Path $archive -Leaf)" | Set-Content "$archive.sha256" -Encoding ascii
Copy-Item (Join-Path $app "manifest.json") "dist/manifest.json"
Write-Output "Portable ZIP and frozen DOC/DOCX + GUI acceptance: PASS"
