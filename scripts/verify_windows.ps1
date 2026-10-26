param([Parameter(Mandatory)][string]$Archive, [Parameter(Mandatory)][string]$Version)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$Archive = (Resolve-Path $Archive).Path
$clean = Join-Path ([IO.Path]::GetTempPath()) ("Portable проверка с пробелами " + [guid]::NewGuid())
Expand-Archive -Path $Archive -DestinationPath $clean
$exe = Join-Path $clean "CoreDescriptionW2E/CoreDescriptionW2E.exe"
$previousPath = $env:PATH
$previousCwd = Get-Location
try {
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    Set-Location $clean
    foreach ($arguments in @(@("--smoke-test"), @("--cli", "--help"), @("--cli", "--version"), @("--version"), @("--frozen-smoke", "acceptance"))) {
        $process = Start-Process -FilePath $exe -ArgumentList $arguments -PassThru
        if (-not $process.WaitForExit(90000)) { $process.Kill(); throw "Frozen smoke timed out" }
        if ($process.ExitCode -ne 0) { throw "Frozen smoke failed: $($process.ExitCode)" }
    }
    $result = Get-Content "acceptance/smoke-result.json" -Raw | ConvertFrom-Json
    if ($result.status -ne "PASS" -or -not $result.frozen -or $result.version -ne $Version) {
        throw "Unpacked EXE version/acceptance mismatch"
    }
    return $result
} finally {
    Set-Location $previousCwd
    $env:PATH = $previousPath
    Remove-Item $clean -Recurse -Force
}
