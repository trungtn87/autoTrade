-- Dashboard read source for the October 2026 fill-history repair.
-- Original BingX fills remain in bingx_fills and continue to receive live ingest.
-- bingx_fills_rebuilt_20261030 is a previously validated historical snapshot.
-- Prefer live exchange fills over reconstructed rows for the same order.
CREATE OR REPLACE VIEW autotrade_analytics.bingx_fills_dashboard
WITH (security_invoker = true) AS
SELECT trade_id, order_id, symbol, side, position_side, qty, price,
       realized_pnl, fee, timestamp_ms, raw
FROM autotrade_analytics.bingx_fills
UNION ALL
SELECT h.trade_id, h.order_id, h.symbol, h.side, h.position_side, h.qty, h.price,
       h.realized_pnl, h.fee, h.timestamp_ms, h.raw
FROM autotrade_analytics.bingx_fills_rebuilt_20261030 AS h
WHERE NOT EXISTS (
    SELECT 1
    FROM autotrade_analytics.bingx_fills AS live
    WHERE live.symbol = h.symbol AND live.order_id = h.order_id
);

-- Private analytics tables and views must not be served to public API roles.
REVOKE ALL PRIVILEGES ON autotrade_analytics.bingx_fills_dashboard
FROM PUBLIC, anon, authenticated;
