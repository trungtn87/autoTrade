@echo off
REM AutoTrade V3 - Full 3-Year Backtest (BTC/ETH M1)
cd /d "%~dp0"
echo ============================================================
echo Running AutoTrade V3 Full 3-Year Replay Backtest (2023-2026)
echo ============================================================
".venv\Scripts\python.exe" -m autotrade_v3.data_backtest.replay_20final --catalog "catalog/research.duckdb" --out-dir "runs/3year_replay" --start "2023-11-01" --end "2026-11-01"
echo.
echo Replay finished! Results stored in runs/3year_replay/
pause
