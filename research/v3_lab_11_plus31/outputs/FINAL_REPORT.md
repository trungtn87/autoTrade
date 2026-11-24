# Final report — V3_LAB_11 + TV31

## Completed runs

- TV31 registered: 31/31 (18 BTC, 13 ETH); source hashes verified.
- IS parity: PASS for every row; total 2,377 trades and Net BASE 802.04036058 USD.
- Three-year TV31 standalone BASE positive: 30/31.
- PRUNE23 BASE: Net 575.66962703 USD, ROI 57.5670%, M2M DD 41.4229 USD, 3,066 trades.
- TV31 shared portfolio BASE: Net 636.58119298 USD, ROI 63.6581%, M2M DD 238.6354 USD, 3,573 trades.
- Shared 54 BASE: Net 1,212.25082000 USD, ROI 121.2251%, M2M DD 208.8400 USD, 6,639 trades.
- Shared 54 STRESS: Net 780.63980442 USD, ROI 78.0640%, M2M DD 240.9986 USD, 6,660 trades.
- Core execution runtime: 391.9 seconds. Accounting invariants: PASS.

## Validation and fixes

- Fixed missing runtime registration of the frozen `20FINAL_FIXED_V1` parameters before constructing PRUNE23.
- Fixed duplicate legacy display IDs by qualifying old codes with symbol; TV01–TV31 remain unchanged.
- Fixed strategy-detail API routing for legacy IDs containing `/` by using a query parameter.
- Reduced persisted equity from M1/open-position snapshots to verified daily closes; DB is 12.3 MB instead of 1.1 GB.
- Ledger trade counts and Net PnL reconcile exactly to every portfolio summary; 54 strategy rows, zero duplicate IDs, zero null ROI.
- 21 focused source/dashboard tests passed, followed by 17 final regression tests. Browser verification rendered the 54-row run, equity/monthly charts, detail ledger, and filters.

Dashboard reads the persisted SQLite database through the local read-only API. No BingX client is imported or called.
