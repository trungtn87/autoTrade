# Live Terminal (mobile PWA)

This is the **read-only Dashboard** inside the single AutoTrade hosting
application. Open `/mobile` directly; no login for the supported public
redacted mode. `/api/dashboard` contains read-only strategy / BingX history.
There is **no separate Dashboard hosting service**.

BingX order/fill/income/force close/position history is read by a separate
read-only BingX API client. A background hourly scheduler starts with the
unified application and archives into the existing Supabase
`autotrade_analytics` schema. The collector never places BingX orders.
Manual GitHub history backfill is retained only for exceptional catch-up.

Metrics distinguish theoretical strategy ROI (no exchange fees/funding)
from exchange-derived realised PnL and fill fees. If Combo attribution
cannot be proved by exact IDs, results are marked candidate, ambiguous,
conflict or unmapped; never invent a win, loss or ownership.

The public URL can disclose redacted trading summaries and PnL. Raw
exchange IDs and diagnostic details are filtered. Do not place BingX
trading keys in Dashboard-specific code; use separate read-only keys.

The legacy Streamlit and Render deployments were retired. All hosting
settings are in [../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md).
