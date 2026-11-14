@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
"%PY%" -m streamlit run autotrade_v3/dashboard/app.py --server.address 127.0.0.1 --server.headless true %*
