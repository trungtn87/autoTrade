-- Verification for supabase/migrations/20261113000000_v2_five_layer_tables.sql.
-- Run against a throwaway database only. Requires the autotrade_analytics schema.
-- Usage: psql -v ON_ERROR_STOP=1 -f migration.sql -f this_file.sql
\set ON_ERROR_STOP on

-- 1. Tables and key columns exist after the migration.
DO $$
BEGIN
  IF (SELECT count(*) FROM information_schema.tables
      WHERE table_schema='autotrade_analytics'
        AND table_name IN ('strategy_signals','execution_orders','execution_results',
                           'execution_finances','control_logs')) <> 5 THEN
    RAISE EXCEPTION 'expected 5 new tables';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
      WHERE table_schema='autotrade_analytics' AND table_name='execution_finances'
        AND column_name IN ('capital_used','margin_used','leverage','roi_pct')
      HAVING count(*) = 4) THEN
    RAISE EXCEPTION 'execution_finances is missing capital/margin/leverage/roi columns';
  END IF;
END $$;

-- 2. Foreign keys are enforced: an execution order with no signal must fail.
DO $$
BEGIN
  BEGIN
    INSERT INTO autotrade_analytics.execution_orders
      (event_id, client_order_id, step, status)
    VALUES ('missing-event', 'cid-orphan', 'ENTRY_POST', 'SENT');
    RAISE EXCEPTION 'orphan execution_order was accepted';
  EXCEPTION WHEN foreign_key_violation THEN
    NULL; -- expected
  END;
END $$;

-- 3. Happy path: one signal, one order, one result, one finance row, one log row.
INSERT INTO autotrade_analytics.strategy_signals
  (event_id, symbol, combo, side, signal_time, entry, tp, sl)
VALUES ('evt-1', 'BTC-USDT', 'C1', 'BUY', now(), 60000, 61000, 59000);

INSERT INTO autotrade_analytics.execution_orders
  (event_id, client_order_id, bingx_order_id, step, status, executed_qty, avg_price,
   bingx_endpoint, bingx_queried_at, raw_response)
VALUES ('evt-1', 'cid-1', '1001', 'ENTRY_POST', 'FILLED', 0.01, 60010,
        '/openApi/swap/v2/trade/order', now(), '{"code":0}');

INSERT INTO autotrade_analytics.execution_results
  (execution_order_id, close_order_id, closed_qty, close_price, exit_reason,
   bingx_endpoint, bingx_queried_at)
SELECT id, '1002', 0.01, 61000, 'TP', '/openApi/swap/v2/trade/order', now()
FROM autotrade_analytics.execution_orders WHERE client_order_id='cid-1';

INSERT INTO autotrade_analytics.execution_finances
  (execution_order_id, capital_used, margin_used, leverage, fee, funding,
   realized_pnl, roi_pct, bingx_endpoint, bingx_queried_at)
SELECT id, 600, 12, 50, 0.3, 0, 9.5, 1.58, '/openApi/swap/v2/user/income', now()
FROM autotrade_analytics.execution_orders WHERE client_order_id='cid-1';

INSERT INTO autotrade_analytics.control_logs
  (layer, symbol, event_id, order_id, step, error_code, message, details, discord_status)
VALUES ('L3', 'BTC-USDT', 'evt-1', '1001', 'SL_POST', '101400', 'loi thu nghiem', '{}', 'pending');

-- 4. Invalid values are rejected by CHECK constraints.
DO $$
BEGIN
  BEGIN
    INSERT INTO autotrade_analytics.strategy_signals
      (event_id, symbol, combo, side, signal_time, entry, tp, sl)
    VALUES ('evt-bad', 'DOGE-USDT', 'C1', 'BUY', now(), 1, 2, 0);
    RAISE EXCEPTION 'bad symbol was accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

-- 5. Re-running the migration in the same database must succeed and keep data.
\i supabase/migrations/20261113000000_v2_five_layer_tables.sql

DO $$
BEGIN
  IF (SELECT count(*) FROM autotrade_analytics.execution_orders) <> 1 THEN
    RAISE EXCEPTION 'rerun changed execution_orders rows';
  END IF;
END $$;

SELECT 'V2_FIVE_LAYER_TABLES_TEST: PASS' AS result;
