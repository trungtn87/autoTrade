-- OBSERVE ONLY: Gemini analysis storage. No trading data or existing tables changed.
-- ai_reviews contains snapshot-as-of-signal; ai_post_reviews contains verified CLOSED snapshots.
BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE SCHEMA IF NOT EXISTS autotrade_analytics;

CREATE TABLE autotrade_analytics.ai_reviews (
    event_id varchar(120) PRIMARY KEY,
    symbol text NOT NULL,
    combo text NOT NULL,
    side text NOT NULL,
    timeframe text NOT NULL,
    signal_close_ms bigint NOT NULL,
    mode text NOT NULL DEFAULT 'OBSERVE' CHECK (mode = 'OBSERVE'),
    ai_decision text NOT NULL CHECK (ai_decision IN ('ALLOW','BLOCK','UNSURE','ERROR','INVALID','TIMEOUT')),
    final_action text NOT NULL DEFAULT 'NO_INTERVENTION' CHECK (final_action = 'NO_INTERVENTION'),
    confidence numeric CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    smc_alignment text CHECK (smc_alignment IS NULL OR smc_alignment IN ('ALIGNED','MIXED','CONFLICT','UNKNOWN')),
    news_risk text CHECK (news_risk IS NULL OR news_risk IN ('NONE','LOW','HIGH','UNKNOWN')),
    news_status text NOT NULL DEFAULT 'UNAVAILABLE',
    risk_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
    input_json jsonb NOT NULL,
    input_hash char(64) NOT NULL,
    output_json jsonb,
    model text,
    prompt_version text NOT NULL,
    rules_version text NOT NULL,
    latency_ms integer,
    tokens_in integer,
    tokens_out integer,
    cost_usd numeric(10,6),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ai_reviews_symbol_combo_idx ON autotrade_analytics.ai_reviews(symbol,combo,created_at DESC);
CREATE INDEX ai_reviews_decision_idx ON autotrade_analytics.ai_reviews(ai_decision,created_at DESC);

CREATE TABLE autotrade_analytics.ai_news_items (
    id bigserial PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('MACRO','CRYPTO')),
    symbol text,
    title text NOT NULL,
    event_ms bigint,
    published_ms bigint,
    source_url text NOT NULL,
    source_name text,
    fetched_ms bigint NOT NULL,
    dedup_key char(64) NOT NULL UNIQUE
);
-- Post reviews use one immutable row per result revision (corrections cannot rewrite prior reasoning).
CREATE TABLE autotrade_analytics.ai_post_reviews (
    event_id varchar(120) NOT NULL,
    trade_result_revision bigint NOT NULL,
    entry_order_id text,
    exit_order_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    outcome text,
    pnl numeric,
    fees numeric,
    funding numeric,
    roi numeric,
    primary_cause text,
    pre_trade_assessment text CHECK (pre_trade_assessment IN ('CORRECT','INCORRECT','INCONCLUSIVE')),
    output_json jsonb,
    input_hash char(64) NOT NULL,
    model text,
    prompt_version text NOT NULL,
    latency_ms integer,
    tokens_in integer,
    tokens_out integer,
    cost_usd numeric(10,6),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(event_id,trade_result_revision)
);
CREATE INDEX ai_post_reviews_created_idx ON autotrade_analytics.ai_post_reviews(created_at DESC);

-- Reserved for a future GATE experiment; OBSERVE never writes simulated trades.
CREATE TABLE autotrade_analytics.ai_shadow_outcomes (
    event_id varchar(120) PRIMARY KEY REFERENCES autotrade_analytics.ai_reviews(event_id),
    entry numeric NOT NULL,
    tp numeric NOT NULL,
    sl numeric NOT NULL,
    exit_mode text NOT NULL,
    status text NOT NULL CHECK (status IN ('PENDING','TP','SL','TRAIL','AMBIGUOUS','EXPIRED_NO_EXIT')),
    exit_ms bigint,
    exit_price numeric,
    gross_pct numeric,
    est_fee_pct numeric,
    net_pct numeric,
    mfe_pct numeric,
    mae_pct numeric,
    sim_version text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION autotrade_analytics.reject_ai_audit_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'ai_audit_immutable';
END;
$$;
CREATE TRIGGER ai_reviews_immutable BEFORE UPDATE OR DELETE ON autotrade_analytics.ai_reviews
    FOR EACH ROW EXECUTE FUNCTION autotrade_analytics.reject_ai_audit_mutation();
CREATE TRIGGER ai_post_reviews_immutable BEFORE UPDATE OR DELETE ON autotrade_analytics.ai_post_reviews
    FOR EACH ROW EXECUTE FUNCTION autotrade_analytics.reject_ai_audit_mutation();
REVOKE ALL ON FUNCTION autotrade_analytics.reject_ai_audit_mutation() FROM PUBLIC, anon, authenticated;
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['ai_reviews','ai_news_items','ai_post_reviews','ai_shadow_outcomes']
    LOOP
        EXECUTE format('ALTER TABLE autotrade_analytics.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('REVOKE ALL ON TABLE autotrade_analytics.%I FROM PUBLIC, anon, authenticated',t);
    END LOOP;
END;
$$;
REVOKE ALL ON SEQUENCE autotrade_analytics.ai_news_items_id_seq FROM PUBLIC, anon, authenticated;
COMMENT ON TABLE autotrade_analytics.ai_reviews IS
    'OBSERVE-only immutable Gemini judgment from a signal-time market snapshot. final_action never represents exchange fill.';
COMMENT ON TABLE autotrade_analytics.ai_post_reviews IS
    'Post-trade reasoning from independently verified CLOSED intent results only; no inferred closure from BingX merged position.';
COMMIT;
