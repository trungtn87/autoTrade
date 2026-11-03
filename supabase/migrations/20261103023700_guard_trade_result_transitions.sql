-- Additive PR #55 review fix; no historical data updates or correction bypass.
BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE FUNCTION autotrade_analytics.guard_result_transition() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
DECLARE
    before_row jsonb := OLD.payload;
    after_row jsonb := NEW.payload;
    old_status text := OLD.payload->>'status';
    new_status text := NEW.payload->>'status';
    field text;
    before_number numeric;
    after_number numeric;
    old_refs jsonb;
    progressed boolean := old_status IS DISTINCT FROM new_status;
BEGIN
    -- Normal writes only advance. CLOSED never becomes UNRESOLVED/OPEN/PARTIAL.
    IF (CASE old_status
        WHEN 'UNRESOLVED' THEN new_status IN ('UNRESOLVED', 'OPEN', 'PARTIAL', 'CLOSED')
        WHEN 'OPEN' THEN new_status IN ('OPEN', 'PARTIAL', 'CLOSED')
        WHEN 'PARTIAL' THEN new_status IN ('PARTIAL', 'CLOSED')
        WHEN 'CLOSED' THEN new_status = 'CLOSED'
        ELSE false END) IS NOT TRUE THEN
        RAISE EXCEPTION 'status_regression_correction_required' USING ERRCODE = '23514';
    END IF;
    FOREACH field IN ARRAY ARRAY['event_id', 'symbol', 'side', 'entry_order_id', 'combo',
                                 'verification_source', 'source'] LOOP
        IF nullif(before_row->>field, '') IS NOT NULL
           AND before_row->>field IS DISTINCT FROM after_row->>field THEN
            RAISE EXCEPTION 'confirmed_identity_changed' USING ERRCODE = '23514';
        END IF;
    END LOOP;
    FOREACH field IN ARRAY ARRAY['opened_ms', 'closed_ms', 'outcome', 'margin_evidence_ref',
                                 'contract_version'] LOOP
        IF before_row->>field IS NOT NULL
           AND before_row->>field IS DISTINCT FROM after_row->>field THEN
            RAISE EXCEPTION 'verified_fact_correction_required' USING ERRCODE = '23514';
        END IF;
    END LOOP;
    FOREACH field IN ARRAY ARRAY['opened_qty', 'closed_qty'] LOOP
        before_number := (before_row->>field)::numeric;
        after_number := (after_row->>field)::numeric;
        IF before_number IS NOT NULL AND (after_number IS NULL OR after_number < before_number)
           OR old_status = 'CLOSED' AND before_number IS DISTINCT FROM after_number THEN
            RAISE EXCEPTION 'quantity_correction_required' USING ERRCODE = '23514';
        END IF;
        progressed := progressed OR before_number IS DISTINCT FROM after_number;
    END LOOP;
    FOREACH field IN ARRAY ARRAY['pnl', 'fees', 'margin', 'roi'] LOOP
        IF before_row->>field IS NOT NULL
           AND (before_row->>field)::numeric IS DISTINCT FROM (after_row->>field)::numeric THEN
            RAISE EXCEPTION 'verified_money_correction_required' USING ERRCODE = '23514';
        END IF;
    END LOOP;
    IF before_row->'money_verified' = 'true'::jsonb
       AND (after_row->'money_verified') IS DISTINCT FROM 'true'::jsonb THEN
        RAISE EXCEPTION 'money_verification_would_be_lost' USING ERRCODE = '23514';
    END IF;
    FOREACH field IN ARRAY ARRAY['evidence_refs', 'money_evidence_refs', 'exit_types'] LOOP
        old_refs := CASE WHEN jsonb_typeof(before_row->field) = 'array'
                        THEN before_row->field ELSE '[]'::jsonb END;
        IF old_refs <> '[]'::jsonb AND ((after_row->field) @> old_refs) IS NOT TRUE THEN
            RAISE EXCEPTION 'evidence_would_be_lost' USING ERRCODE = '23514';
        END IF;
    END LOOP;
    old_refs := CASE WHEN jsonb_typeof(before_row->'evidence_refs') = 'array'
                    THEN before_row->'evidence_refs' ELSE '[]'::jsonb END;
    IF progressed AND (jsonb_typeof(after_row->'evidence_refs') IS DISTINCT FROM 'array'
                      OR ((after_row->'evidence_refs') <@ old_refs) IS NOT FALSE) THEN
        RAISE EXCEPTION 'progress_requires_new_evidence' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION autotrade_analytics.guard_result_transition() FROM PUBLIC, anon, authenticated;
CREATE TRIGGER trade_results_transition_guard BEFORE UPDATE
    ON autotrade_analytics.trade_results FOR EACH ROW
    WHEN (OLD.revision > 0 OR NEW.revision > 0)
    EXECUTE FUNCTION autotrade_analytics.guard_result_transition();
COMMENT ON FUNCTION autotrade_analytics.guard_result_transition() IS
    'Append-only normal observation contract; verified fact retractions need a separately reviewed correction mechanism, never a payload flag.';
COMMIT;
