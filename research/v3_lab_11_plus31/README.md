# V3_LAB_11 + TV31 (local research only)

This package integrates the frozen 31 TradingView configurations with the unchanged
`V3_LAB_11_PRUNE23@11.0.0` research portfolio. It never imports a BingX client and
does not change the live strategy registry.

Run preflight:

```powershell
.\.venv\Scripts\python.exe research\v3_lab_11_plus31\freeze\LOCAL_SELECTION_CHECK.py --root D:\AutoTrade
```

Run the historical study:

```powershell
.\.venv\Scripts\python.exe research\v3_lab_11_plus31\run_backtest.py
```
