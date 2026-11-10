@echo off
REM AutoTrade V3 - Gate B2 Cross-Venue Source Sensitivity
cd /d "%~dp0"
echo ============================================================
echo Running AutoTrade V3 Gate B2 Source Sensitivity Analysis
echo ============================================================
".venv\Scripts\python.exe" -m autotrade_v3.data_backtest.compare_sources --run17-btc "runs/run17_events/BTCUSDT_run17_events.csv" --run17-eth "runs/run17_events/ETHUSDT_run17_events.csv" --hybrid-events "runs/3year_replay/signal_events.csv" --out "reports/v3_b2_result.json"
echo.
echo Gate B2 sensitivity report complete. See reports/v3_b2_result.json
pause
