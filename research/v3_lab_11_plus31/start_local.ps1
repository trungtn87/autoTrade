$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Missing project virtualenv: $python" }
Start-Process -FilePath $python -ArgumentList @("-m", "uvicorn", "research.v3_lab_11_plus31.api:app", "--host", "127.0.0.1", "--port", "8765") -WorkingDirectory $root -WindowStyle Hidden
Start-Process -FilePath $python -ArgumentList @("-m", "autotrade_v3.dashboard", "serve") -WorkingDirectory $root -WindowStyle Hidden
Write-Host "Research API: http://127.0.0.1:8765"
Write-Host "Dashboard:    http://127.0.0.1:8501"
