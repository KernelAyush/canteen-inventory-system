[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) { throw 'Run .\scripts\setup.ps1 first.' }
    & $venvPython scripts/check_environment.py --test
    if ($LASTEXITCODE -ne 0) { throw 'Test database preflight failed.' }
    & $venvPython -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed. Read the pytest output above.' }
} finally {
    Pop-Location
}
