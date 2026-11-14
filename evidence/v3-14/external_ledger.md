# B7: External Ledger Import & Reconciliation

## Real Owner Independent Backtest Reconciliation
- Status: **BLOCKED_ON_OWNER** (`OWNER_EXTERNAL_LEDGER` is empty; no external Owner ledger file exists in workspace).

## [SYNTHETIC] External Ledger Import + 5-Layer Diff Demonstration
- Imported synthetic run_id: `R_EXT_968295ba0a66` (`source=EXTERNAL`, `synthetic=True`)
- Missing optional columns recorded: `['leg_id', 'trade_id', 'combo_id', 'timeframe', 'gross_pnl', 'gross_pnl_raw', 'fee_entry', 'fee_exit', 'spread_cost', 'slippage_cost', 'funding_cost', 'entry_px_raw', 'entry_px_fill', 'exit_px_trigger', 'exit_px_fill', 'ambiguity_type']`
- Diff categories (`R_SYN_INTERNAL` vs `R_EXT_968295ba0a66`): `{"IDENTICAL": 0, "DATA": 0, "SIGNAL": 4, "ENTRY": 0, "AMBIGUITY": 0, "EXIT_LOGIC": 0, "COST": 0, "UNEXPLAINED": 0}`
- `actual_delta_net = -0.340000`, `expected_delta_net = -0.340000`, `invariant_diff = 1.11e-16`
