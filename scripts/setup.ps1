# Run from PowerShell: .\scripts\setup.ps1
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $pythonCommand = $null
    $pythonArguments = @()
    foreach ($candidate in @('py', 'python')) {
        if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) { continue }
        $candidateArguments = @()
        if ($candidate -eq 'py') { $candidateArguments = @('-3') }
        & $candidate @candidateArguments -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) {
            $pythonCommand = $candidate
            $pythonArguments = $candidateArguments
            break
        }
    }
    if (-not $pythonCommand) {
        throw 'Python 3.11+ was not found. Check py --list and your installed Python before installing another copy.'
    }
    & $pythonCommand @pythonArguments --version
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        & $pythonCommand @pythonArguments -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the project virtual environment.' }
    }
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    & $venvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check network access and Python version.' }
    if (-not (Test-Path '.env')) {
        Copy-Item '.env.example' '.env'
        Write-Host 'Created .env. Set your dedicated database URLs before migrating.'
    } else {
        Write-Host 'Existing .env retained.'
    }
    $psql = Get-Command psql -ErrorAction SilentlyContinue
    if ($psql) {
        & $psql.Source --version
    } else {
        Write-Host 'psql is not on PATH. An existing PostgreSQL install may still be available through SQL Shell or pgAdmin.'
        Write-Host 'Typical path: C:\Program Files\PostgreSQL\<version>\bin\psql.exe'
    }
    Write-Host 'Setup complete. Follow README database creation, then run migrations and scripts\run.ps1.'
} finally {
    Pop-Location
}
