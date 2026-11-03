-- PR 1 only. Apply after 20261103010119, with Owner approval; never on startup.
-- No raw/manual/history rows are changed. Existing snapshots start at revision 0.
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE autotrade_analytics.trade_results
    ADD COLUMN revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0);

-- NOT VALID preserves any pre-existing draft rows. New writes must comply;
-- validate historical rows separately after read-only reconciliation.
ALTER TABLE autotrade_analytics.trade_results ADD CONSTRAINT result_closed_identity
    CHECK (status <> 'CLOSED' OR
        (closed_qty > 0 AND nullif(symbol, '') IS NOT NULL AND nullif(combo, '') IS NOT NULL
         AND side IN ('BUY', 'SELL') AND nullif(entry_order_id, '') IS NOT NULL
         AND nullif(verification_source, '') IS NOT NULL) IS TRUE) NOT VALID;

ALTER TABLE autotrade_analytics.trade_results ADD CONSTRAINT result_contract_v1
    CHECK (NOT (payload ? 'contract_version') OR (
        payload->'contract_version' = '1'::jsonb
        AND payload->>'verification_source' = verification_source
        AND jsonb_typeof(payload->'evidence_refs') = 'array'
        AND payload->'evidence_refs' <> '[]'::jsonb
        AND (pnl IS NULL OR (fees IS NOT NULL
            AND payload->'money_verified' = 'true'::jsonb
            AND jsonb_typeof(payload->'money_evidence_refs') = 'array'
            AND payload->'money_evidence_refs' <> '[]'::jsonb
            AND (payload->'evidence_refs') @> (payload->'money_evidence_refs')))
        AND (margin IS NULL OR (margin > 0
            AND jsonb_typeof(payload->'margin_evidence_ref') = 'string'
            AND (payload->'evidence_refs') @> jsonb_build_array(payload->>'margin_evidence_ref')))
        AND (roi IS NULL OR abs(roi - 100*pnl/nullif(margin, 0)) <= 0.000001)
        AND (outcome IS NULL OR outcome = CASE
            WHEN pnl > 0 THEN 'WIN' WHEN pnl < 0 THEN 'LOSS' ELSE 'BREAKEVEN' END)
    ) IS TRUE) NOT VALID;

-- A verified entry cannot appear as two completed trading intents. If this
-- fails on an existing staging DB, stop and reconcile; never delete duplicates.
CREATE UNIQUE INDEX trade_results_one_closed_entry_idx
    ON autotrade_analytics.trade_results(symbol, entry_order_id) WHERE status = 'CLOSED';

CREATE TABLE autotrade_analytics.trade_result_receipts (
    source varchar(80) NOT NULL CHECK (length(source) > 0),
    evidence_id varchar(240) NOT NULL CHECK (length(evidence_id) > 0),
    event_id varchar(120) NOT NULL REFERENCES autotrade_analytics.trade_results(event_id),
    revision bigint NOT NULL CHECK (revision > 0),
    snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(source, evidence_id),
    UNIQUE(event_id, revision),
    CHECK ((snapshot->>'event_id' = event_id) IS TRUE)
);

-- Legacy collector merge() does not increment revisions. Once the new writer
-- owns a row, reject that overwrite instead of losing V2 evidence. PR 3 must
-- adapt collector writes to the same contract before enabling the new writer.
CREATE FUNCTION autotrade_analytics.guard_result_revision() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.event_id IS DISTINCT FROM OLD.event_id
       OR NEW.revision <> OLD.revision + 1 THEN
        RAISE EXCEPTION 'result_revision_conflict' USING ERRCODE = '40001';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION autotrade_analytics.guard_result_revision() FROM PUBLIC, anon, authenticated;
CREATE TRIGGER trade_results_revision_guard BEFORE UPDATE
    ON autotrade_analytics.trade_results FOR EACH ROW
    WHEN (OLD.revision > 0 OR NEW.revision > 0)
    EXECUTE FUNCTION autotrade_analytics.guard_result_revision();
ALTER TABLE autotrade_analytics.trade_result_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON autotrade_analytics.trade_result_receipts FROM PUBLIC, anon, authenticated;
COMMENT ON COLUMN autotrade_analytics.trade_results.revision IS
    '0 = legacy draft. Positive revisions use atomic snapshot/receipt storage; never reset on rollback.';
COMMENT ON TABLE autotrade_analytics.trade_result_receipts IS
    'Private result snapshots and idempotency receipts. Preserve on rollback; no secrets/raw API headers.';
COMMIT;
