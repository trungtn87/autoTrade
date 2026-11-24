# V3_LAB_11 PLUS44 - final comparison

All portfolio values below come from actual shared-capital `UnifiedSimulator/BINGX_PORTFOLIO` replays.

| Portfolio | Net BASE | Net STRESS | ROI BASE | ROI STRESS | M2M DD BASE | M2M DD STRESS | Trades BASE | Trades STRESS | Y1/Y2/Y3 BASE | Y1/Y2/Y3 STRESS | Peak legs BASE/STRESS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|
| PRUNE23 | 575.66962703 | 358.63913019 | 57.5670% | 35.8639% | 41.4229 | 46.3791 | 3,066 | 3,077 | 192.5866 / 195.8215 / 187.2616 | 130.3823 / 109.7458 / 118.5111 | 8 / 8 |
| PLUS44 | 1168.42374391 | 817.47376382 | 116.8424% | 81.7474% | 131.4767 | 153.9592 | 5,208 | 5,219 | 372.1928 / 615.9861 / 180.2449 | 274.0486 / 481.3283 / 62.0968 | 15 / 15 |
| PLUS54 | 1212.25082000 | 780.63980442 | 121.2251% | 78.0640% | 208.8400 | 240.9986 | 6,639 | 6,660 | 459.4511 / 730.9974 / 21.8024 | 343.4655 / 560.4846 / -123.3103 | 18 / 18 |

## Resource usage

- Runtime: 337.2 seconds.
- Peak aggregate Python working set: 1802.6 MB.
- Average CPU utilization over total logical capacity: 2.07%.
- Peak sampled CPU utilization over total logical capacity: 17.40%.

## Accounting and attribution

- Accounting invariants: PASS.
- `TV21_STANDALONE` is explicitly labeled independent; `TV21_ONLY` and `PRUNE23_PLUS_TV21` are shared portfolio executions.
- TV21 standalone Net BASE sum is `592.75411688`; shared-portfolio allocated Net BASE also totals `592.75411688`, but all 21 per-code allocations differ because the engine accounts through merged BTC/ETH side positions. Standalone has 21 positive codes; portfolio allocation has 20.
- See `TV21_STANDALONE_VS_PORTFOLIO.csv` for per-code allocation differences.
- The previous PLUS54 database rows and artifacts were preserved.
