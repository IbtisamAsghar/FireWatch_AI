param([switch]$SkipPostgres, [int]$PostgresPort = 5432)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 virtual environment creation failed' }
}
$pythonPath = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $pythonPath -m pip install --require-hashes -r requirements-dev.lock
if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed' }
& $pythonPath -m pip install -e . --no-deps --no-build-isolation
if ($LASTEXITCODE -ne 0) { throw 'Editable package installation failed' }
if (-not $SkipPostgres) {
    & $pythonPath scripts/native_postgres.py setup --port $PostgresPort
    if ($LASTEXITCODE -ne 0) { throw 'Native PostgreSQL setup failed' }
    & .\.venv\Scripts\firewatch.exe check-config
    if ($LASTEXITCODE -ne 0) { throw 'Configuration validation failed' }
    & .\.venv\Scripts\firewatch.exe db-check
    if ($LASTEXITCODE -ne 0) { throw 'Database connectivity check failed' }
}
