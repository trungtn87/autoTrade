-- Additive private Gemini shadow feedback; no real trade or execution tables touched.
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE autotrade_analytics.ai_shadow_outcomes
  ADD COLUMN IF NOT EXISTS evidence_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS assessment text,
  ADD COLUMN IF NOT EXISTS feedback_json jsonb,
  ADD COLUMN IF NOT EXISTS feedback_model text,
  ADD COLUMN IF NOT EXISTS feedback_at timestamptz,
  ADD COLUMN IF NOT EXISTS feedback_error text,
  ADD COLUMN IF NOT EXISTS feedback_attempted_at timestamptz;
COMMENT ON COLUMN autotrade_analytics.ai_shadow_outcomes.gross_pct IS
  'Hypothetical unleveraged price move at signal Entry and static TP/SL. NOT realized PnL or ROI.';
COMMENT ON COLUMN autotrade_analytics.ai_shadow_outcomes.assessment IS
  'Deterministic comparison of ALLOW/BLOCK with hypothetical static TP/SL; never real trade verification.';
REVOKE ALL ON TABLE autotrade_analytics.ai_shadow_outcomes FROM PUBLIC, anon, authenticated;
ALTER TABLE autotrade_analytics.ai_shadow_outcomes ENABLE ROW LEVEL SECURITY;
COMMIT;
