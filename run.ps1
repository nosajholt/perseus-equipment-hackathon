# Launch the Perseus BI Dashboard.
# Open http://127.0.0.1:8000 once uvicorn reports it is running.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Starting Perseus BI Dashboard at http://127.0.0.1:8000 ..." -ForegroundColor Cyan
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
