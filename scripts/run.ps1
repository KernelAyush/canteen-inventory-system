[CmdletBinding()]
param([ValidateRange(1024,65535)][int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) { throw 'Run .\scripts\setup.ps1 first.' }
    & $venvPython scripts/check_environment.py
    if ($LASTEXITCODE -ne 0) { throw 'Preflight failed. Resolve the message above before launching.' }
    Write-Host "Opening server at http://127.0.0.1:$Port (Ctrl+C stops it)."
    & $venvPython -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Server stopped with an error. Check the log above; try another -Port if necessary.' }
} finally {
    Pop-Location
}
