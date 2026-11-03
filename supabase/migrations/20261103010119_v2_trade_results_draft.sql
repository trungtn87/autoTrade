-- DRAFT: Owner-approved application only; never executed on startup.
-- Existing raw and manual provenance tables/views remain untouched.
BEGIN;
CREATE SCHEMA IF NOT EXISTS autotrade_analytics;
CREATE TABLE autotrade_analytics.trade_results (
    event_id varchar(120) PRIMARY KEY,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    updated_at timestamptz NOT NULL DEFAULT now(),
    entry_order_id text GENERATED ALWAYS AS (payload->>'entry_order_id') STORED,
    symbol text GENERATED ALWAYS AS (payload->>'symbol') STORED,
    combo text GENERATED ALWAYS AS (payload->>'combo') STORED,
    side text GENERATED ALWAYS AS (payload->>'side') STORED,
    opened_ms bigint GENERATED ALWAYS AS ((payload->>'opened_ms')::bigint) STORED,
    closed_ms bigint GENERATED ALWAYS AS ((payload->>'closed_ms')::bigint) STORED,
    status text GENERATED ALWAYS AS (payload->>'status') STORED NOT NULL,
    outcome text GENERATED ALWAYS AS (payload->>'outcome') STORED,
    exit_types jsonb GENERATED ALWAYS AS (payload->'exit_types') STORED,
    opened_qty numeric GENERATED ALWAYS AS ((payload->>'opened_qty')::numeric) STORED,
    closed_qty numeric GENERATED ALWAYS AS ((payload->>'closed_qty')::numeric) STORED,
    pnl numeric GENERATED ALWAYS AS ((payload->>'pnl')::numeric) STORED,
    fees numeric GENERATED ALWAYS AS ((payload->>'fees')::numeric) STORED,
    margin numeric GENERATED ALWAYS AS ((payload->>'margin')::numeric) STORED,
    roi numeric GENERATED ALWAYS AS ((payload->>'roi')::numeric) STORED,
    verification_source text GENERATED ALWAYS AS (payload->>'source') STORED,
    CHECK ((payload ? 'event_id' AND payload->>'event_id' = event_id) IS TRUE),
    CHECK (status IN ('OPEN', 'PARTIAL', 'CLOSED', 'UNRESOLVED')),
    CHECK (outcome IS NULL OR outcome IN ('WIN', 'LOSS', 'BREAKEVEN')),
    CHECK (opened_qty >= 0 AND closed_qty >= 0),
    CHECK (status <> 'CLOSED' OR (opened_ms IS NOT NULL AND closed_ms IS NOT NULL AND closed_ms >= opened_ms
        AND opened_qty IS NOT NULL AND closed_qty IS NOT NULL AND opened_qty > 0 AND abs(opened_qty-closed_qty) <= greatest(0.000000001, opened_qty*0.000001))),
    CHECK (outcome IS NULL OR (status = 'CLOSED' AND pnl IS NOT NULL)),
    CHECK (roi IS NULL OR (status = 'CLOSED' AND margin IS NOT NULL AND margin > 0 AND pnl IS NOT NULL))
);
-- Entry ID is intentionally not unique: ambiguous many-intent mappings are
-- retained as UNRESOLVED for investigation, never counted as completed trades.
CREATE INDEX trade_results_entry_idx ON autotrade_analytics.trade_results(entry_order_id);
CREATE INDEX trade_results_closed_idx ON autotrade_analytics.trade_results(closed_ms DESC)
    WHERE status = 'CLOSED';
CREATE INDEX trade_results_combo_idx ON autotrade_analytics.trade_results(symbol, combo, status);
ALTER TABLE autotrade_analytics.trade_results ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON autotrade_analytics.trade_results FROM PUBLIC, anon, authenticated;
COMMENT ON TABLE autotrade_analytics.trade_results IS
    'V2 intent ledger; generated report columns from one evidence payload. Unknown money stays NULL. Backend owner only; no public Data API grant.';
COMMIT;
