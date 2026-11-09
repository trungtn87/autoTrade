-- Separate BingX-backed AI feedback from the ledger-only ai_post_reviews contract.
-- Additive, private, and OBSERVE only. No trading orders or existing trade rows touched.
BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE TABLE IF NOT EXISTS autotrade_analytics.ai_bingx_post_reviews (
    event_id varchar(120) NOT NULL REFERENCES autotrade_analytics.ai_reviews(event_id),
    evidence_hash char(64) NOT NULL,
    entry_order_id text NOT NULL,
    symbol text NOT NULL,
    combo text NOT NULL,
    closed_ms bigint NOT NULL,
    evidence_quality text NOT NULL CHECK (evidence_quality IN (
        'BINGX_VERIFIED_EXIT','BINGX_VERIFIED_EXIT_ESTIMATED_MONEY','MERGED_POSITION_ESTIMATE'
    )),
    outcome text NOT NULL CHECK (outcome IN ('WIN','LOSS','BREAKEVEN')),
    pnl numeric,
    roi numeric,
    evidence_json jsonb NOT NULL,
    ai_decision text NOT NULL CHECK (ai_decision IN ('ALLOW','BLOCK')),
    state text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','READY','FAILED')),
    attempts smallint NOT NULL DEFAULT 1 CHECK (attempts BETWEEN 1 AND 3),
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    assessment text CHECK (assessment IN ('CORRECT','INCORRECT','INCONCLUSIVE')),
    analysis_json jsonb,
    model text,
    prompt_version text,
    error_code text,
    PRIMARY KEY (event_id, evidence_hash)
);
CREATE INDEX IF NOT EXISTS ai_bingx_post_reviews_recent_idx
    ON autotrade_analytics.ai_bingx_post_reviews (completed_at DESC)
    WHERE state='READY';
CREATE INDEX IF NOT EXISTS ai_bingx_post_reviews_learning_idx
    ON autotrade_analytics.ai_bingx_post_reviews (symbol, combo, completed_at DESC)
    WHERE state='READY';
ALTER TABLE autotrade_analytics.ai_bingx_post_reviews ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE autotrade_analytics.ai_bingx_post_reviews FROM PUBLIC, anon, authenticated;
COMMENT ON TABLE autotrade_analytics.ai_bingx_post_reviews IS
    'Independent Gemini post-EXIT lessons from Dashboard/BingX DB projection; never the verified lifecycle ledger. Estimated merged outcomes remain inconclusive.';
COMMIT;
