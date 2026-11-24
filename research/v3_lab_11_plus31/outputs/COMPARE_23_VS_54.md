# Compare PRUNE23 vs PRUNE23 + TV31

| BASE portfolio | Net PnL | ROI | M2M DD | Trades | Peak concurrent legs |
|---|---:|---:|---:|---:|---:|
| PRUNE23_ONLY | 575.66962703 | 57.5670% | 41.4229 | 3,066 | 8 |
| TV31_ONLY | 636.58119298 | 63.6581% | 238.6354 | 3,573 | 16 |
| PRUNE23_PLUS_TV31 | 1,212.25082000 | 121.2251% | 208.8400 | 6,639 | 18 |

- Delta 23 → 54: Net `+636.58119298`, M2M DD `+167.4171`, trades `+3,573`.
- The 54 result is an actual shared-capital `UnifiedSimulator/BINGX_PORTFOLIO` replay, not a label applied to an independent-PnL sum.
- In this replay, the combined accepted/skipped totals equal the component totals, so no extra cross-group admission rejection occurred. Merged BTC/ETH side positions were still processed by the shared account/VWAP implementation.
- TP/SL attribution in the 54 run: TP `2,637`, SL `3,992`, end-of-window force-close `10`. No TP/SL parameters were changed.
