@echo off
REM AutoTrade V3 - Gate B1 Parity Verification against V2 Reference
cd /d "%~dp0"
echo ============================================================
echo Running AutoTrade V3 Gate B1 Parity Verification vs V2
echo ============================================================
".venv\Scripts\python.exe" -m autotrade_v3.data_backtest.verify_run17 --btc "data/BTCUSDT_RUN17_M15.csv" --eth "data/ETHUSDT_RUN17_M15.csv" --output "reports/v3_b1_result.json"
echo.
echo Gate B1 verification complete. See reports/v3_b1_result.json
pause
