# V3-13 Historical Sensitivity Reproduction (B3)

- **Commit SHA:** `a4b7555e77871e62007065e943a8c335971ceeb5`
- **Config:** Mode C (`BINGX_PORTFOLIO`), `LEGACY` ambiguity policy, `fee_taker=0.0005`, `spread=0`, uniform slippage `{0, 1, 3, 5}` bps.
- **Historical Reference:** `runs/v3_audited/sensitivity_analysis.json`

| Slippage | Hist Legs | Hist Net PnL | New Legs | New Net PnL | Diff |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0 bps (Baseline) | 6265 | 282.1935 | 6265 | 282.1935 | -0.0000 |
| 1 bps (Scenario S1) | 6270 | 199.4714 | 6270 | 199.4714 | +0.0000 |
| 3 bps (Scenario S2) | 6273 | 38.3273 | 6273 | 38.3273 | -0.0000 |
| 5 bps (Scenario S3) | 6289 | -156.5989 | 6289 | -156.5989 | +0.0000 |
