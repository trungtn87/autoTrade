-- Manual trade reconciliation, dashboard read-only. Do not use this for
-- execution, create exchange orders, allocate shared-position PnL, or mark
-- independently unverified trades as BingX fills.
CREATE TABLE IF NOT EXISTS autotrade_analytics.manual_trade_results (
  source_id text NOT NULL,
  symbol text NOT NULL CHECK (symbol IN ('BTC-USDT','ETH-USDT')),
  source_row integer NOT NULL,
  source_timestamp text NOT NULL,
  matched_time_vn timestamp without time zone NOT NULL,
  combo text NOT NULL,
  reported_side text NOT NULL CHECK (reported_side IN ('BUY','SELL')),
  reported_entry numeric(18,3) NOT NULL,
  reported_tp numeric(18,3) NOT NULL,
  reported_sl numeric(18,3) NOT NULL,
  reported_outcome text NOT NULL CHECK (reported_outcome IN ('W','L')),
  reported_roi_pct numeric(10,2) NOT NULL,
  entry_order_id text,
  match_status text NOT NULL CHECK (match_status IN ('MATCHED','CONFLICT','UNMATCHED')),
  review_reason text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source_id,symbol,source_row)
);
ALTER TABLE autotrade_analytics.manual_trade_results ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON autotrade_analytics.manual_trade_results FROM PUBLIC,anon,authenticated;
CREATE OR REPLACE VIEW autotrade_analytics.manual_trade_results_effective
WITH (security_invoker=true) AS
SELECT m.source_id,
    m.source_row,
    m.symbol,
    (to_timestamp((e.created_ms::numeric / 1000.0)::double precision) AT TIME ZONE 'Asia/Ho_Chi_Minh'::text) AS entry_time_vn,
    e.created_ms AS entry_time_ms,
    m.source_timestamp AS excel_time_original,
    m.entry_order_id,
    l.combo,
    l.side,
    e.position_side,
    e.avg_price::numeric AS entry,
        CASE
            WHEN tp.stop_price > 0::numeric THEN tp.stop_price
            ELSE m.reported_tp
        END AS tp,
        CASE
            WHEN sl.stop_price > 0::numeric THEN sl.stop_price
            ELSE m.reported_sl
        END AS sl,
        CASE
            WHEN tp.stop_price > 0::numeric THEN 'BINGX'::text
            ELSE 'EXCEL'::text
        END AS tp_source,
        CASE
            WHEN sl.stop_price > 0::numeric THEN 'BINGX'::text
            ELSE 'EXCEL'::text
        END AS sl_source,
        CASE
            WHEN grp.entries_count = 1 AND grp.exit_count > 0 AND abs(grp.closed_qty - e.filled_qty) < 0.0000001::double precision AND grp.exit_types_verified AND grp.gross_profit IS NOT NULL THEN
            CASE
                WHEN grp.gross_profit > 0::numeric THEN 'W'::text
                ELSE 'L'::text
            END
            ELSE m.reported_outcome
        END AS outcome,
        CASE
            WHEN grp.entries_count = 1 AND grp.exit_count > 0 AND abs(grp.closed_qty - e.filled_qty) < 0.0000001::double precision AND grp.exit_types_verified AND grp.gross_profit IS NOT NULL THEN 'BINGX'::text
            ELSE 'EXCEL'::text
        END AS outcome_source,
        CASE
            WHEN grp.entries_count = 1 AND grp.exit_count > 0 AND abs(grp.closed_qty - e.filled_qty) < 0.0000001::double precision AND grp.exit_types_verified AND grp.gross_profit IS NOT NULL THEN grp.gross_profit
            ELSE NULL::numeric
        END AS bingx_gross_realized_pnl_usdt,
    m.reported_roi_pct AS excel_roi_pct,
    m.combo AS excel_combo,
    m.reported_side AS excel_side,
    m.reported_entry AS excel_entry,
    m.reported_tp AS excel_tp,
    m.reported_sl AS excel_sl,
    m.reported_outcome AS excel_outcome,
    m.match_status AS excel_comparison_status,
    m.review_reason,
    'BINGX'::text AS entry_source
   FROM autotrade_analytics.manual_trade_results m
     JOIN autotrade_analytics.signal_links l ON l.entry_order_id::text = m.entry_order_id
     JOIN autotrade_analytics.bingx_orders e ON e.order_id::text = m.entry_order_id AND e.symbol::text = m.symbol AND e.status::text = 'FILLED'::text
     LEFT JOIN LATERAL ( SELECT NULLIF(x.raw ->> 'stopPrice'::text, ''::text)::numeric AS stop_price
           FROM autotrade_analytics.bingx_orders x
          WHERE x.symbol::text = e.symbol::text AND x.type::text = 'TAKE_PROFIT_MARKET'::text AND x.side::text <> e.side::text AND (x.raw ->> 'positionID'::text) = (e.raw ->> 'positionID'::text) AND x.created_ms >= e.created_ms AND x.created_ms <= (e.created_ms + 20000)
          ORDER BY x.created_ms
         LIMIT 1) tp ON true
     LEFT JOIN LATERAL ( SELECT NULLIF(x.raw ->> 'stopPrice'::text, ''::text)::numeric AS stop_price
           FROM autotrade_analytics.bingx_orders x
          WHERE x.symbol::text = e.symbol::text AND x.type::text = 'STOP_MARKET'::text AND x.side::text <> e.side::text AND (x.raw ->> 'positionID'::text) = (e.raw ->> 'positionID'::text) AND x.created_ms >= e.created_ms AND x.created_ms <= (e.created_ms + 20000)
          ORDER BY x.created_ms
         LIMIT 1) sl ON true
     LEFT JOIN LATERAL ( SELECT ( SELECT count(*) AS count
                   FROM autotrade_analytics.bingx_orders x_1
                  WHERE x_1.symbol::text = e.symbol::text AND (x_1.raw ->> 'positionID'::text) = (e.raw ->> 'positionID'::text) AND x_1.status::text = 'FILLED'::text AND x_1.side::text = e.side::text AND (x_1.type::text = ANY (ARRAY['MARKET'::character varying, 'LIMIT'::character varying]::text[]))) AS entries_count,
            count(*) FILTER (WHERE x.status::text = 'FILLED'::text AND x.side::text <> e.side::text) AS exit_count,
            COALESCE(sum(x.filled_qty) FILTER (WHERE x.status::text = 'FILLED'::text AND x.side::text <> e.side::text), 0::double precision) AS closed_qty,
            COALESCE(bool_and(x.type::text = ANY (ARRAY['STOP_MARKET'::character varying, 'TAKE_PROFIT_MARKET'::character varying, 'TRAILING_STOP_MARKET'::character varying, 'MARKET'::character varying, 'LIMIT'::character varying]::text[])) FILTER (WHERE x.status::text = 'FILLED'::text AND x.side::text <> e.side::text), false) AS exit_types_verified,
            sum(NULLIF(x.raw ->> 'profit'::text, ''::text)::numeric) FILTER (WHERE x.status::text = 'FILLED'::text AND x.side::text <> e.side::text) AS gross_profit
           FROM autotrade_analytics.bingx_orders x
          WHERE x.symbol::text = e.symbol::text AND (x.raw ->> 'positionID'::text) = (e.raw ->> 'positionID'::text)) grp ON true
  WHERE m.source_id = 'Trade_Log_screenshots_20261030'::text AND m.matched_time_vn >= '2026-10-01 00:00:00'::timestamp without time zone;
REVOKE ALL ON autotrade_analytics.manual_trade_results_effective FROM PUBLIC,anon,authenticated;
CREATE OR REPLACE VIEW autotrade_analytics.manual_dashboard_trade_results
WITH (security_invoker=true) AS
WITH exchange_position AS (
         SELECT v.source_id,
            v.symbol,
            v.source_row,
            v.entry_order_id,
            v.combo,
            v.side,
            v.entry_time_ms,
            v.entry,
            v.outcome,
            v.outcome_source,
            v.excel_tp,
            v.excel_sl,
            v.bingx_gross_realized_pnl_usdt,
            e.raw ->> 'positionID'::text AS position_id,
            e.position_side,
            q.entered_qty,
            q.exited_qty,
            q.exit_ms,
            q.exit_price,
                CASE
                    WHEN v.outcome_source = 'BINGX'::text AND q.exit_price > 0::double precision THEN q.exit_price
                    WHEN v.outcome = 'W'::text THEN v.excel_tp::double precision
                    ELSE v.excel_sl::double precision
                END AS roi_target,
                CASE
                    WHEN v.outcome_source = 'BINGX'::text AND q.exit_price > 0::double precision THEN 'BINGX'::text
                    ELSE 'EXCEL_ESTIMATE'::text
                END AS roi_source
           FROM autotrade_analytics.manual_trade_results_effective v
             JOIN autotrade_analytics.bingx_orders e ON e.order_id::text = v.entry_order_id AND e.symbol::text = v.symbol AND e.status::text = 'FILLED'::text
             LEFT JOIN LATERAL ( SELECT COALESCE(sum(x.filled_qty) FILTER (WHERE x.status::text = 'FILLED'::text AND x.side::text = e.side::text AND (x.type::text = ANY (ARRAY['MARKET'::character varying, 'LIMIT'::character varying]::text[]))), 0::double precision)::numeric AS entered_qty,
                    COALESCE(sum(x.filled_qty) FILTER (WHERE x.status::text = 'FILLED'::text AND x.side::text <> e.side::text AND (x.type::text = ANY (ARRAY['MARKET'::character varying, 'LIMIT'::character varying, 'STOP_MARKET'::character varying, 'TAKE_PROFIT_MARKET'::character varying, 'TRAILING_STOP_MARKET'::character varying]::text[]))), 0::double precision)::numeric AS exited_qty,
                    max(x.updated_ms) FILTER (WHERE x.status::text = 'FILLED'::text AND x.side::text <> e.side::text) AS exit_ms,
                        CASE
                            WHEN sum(x.filled_qty) FILTER (WHERE x.status::text = 'FILLED'::text AND x.side::text <> e.side::text) > 0::double precision THEN sum(x.filled_qty * x.avg_price) FILTER (WHERE x.status::text = 'FILLED'::text AND x.side::text <> e.side::text) / sum(x.filled_qty) FILTER (WHERE x.status::text = 'FILLED'::text AND x.side::text <> e.side::text)
                            ELSE NULL::double precision
                        END AS exit_price
                   FROM autotrade_analytics.bingx_orders x
                  WHERE x.symbol::text = e.symbol::text AND (x.raw ->> 'positionID'::text) = (e.raw ->> 'positionID'::text)) q ON true
        )
 SELECT source_id,
    source_row,
    symbol,
    entry_order_id,
    combo,
    side,
    position_side,
    entry_time_ms,
    exit_ms,
    entry AS entry_price,
    roi_target AS price_for_roi,
    round((100.0::double precision * (roi_target / entry::double precision - 1::double precision) *
        CASE
            WHEN side::text = 'BUY'::text THEN 1
            ELSE '-1'::integer
        END::double precision)::numeric, 4) AS roi_pct,
    roi_source,
    outcome,
        CASE
            WHEN outcome_source = 'BINGX'::text THEN 'BINGX'::text
            ELSE 'EXCEL'::text
        END AS outcome_source,
    bingx_gross_realized_pnl_usdt,
    'CLOSED'::text AS status
   FROM exchange_position
  WHERE entered_qty > 0::numeric AND abs(entered_qty - exited_qty) < 0.0000001 AND exit_ms IS NOT NULL AND exit_ms >= entry_time_ms AND entry > 0::numeric AND roi_target > 0::double precision AND (outcome = 'W'::text AND ((roi_target / entry::double precision - 1::double precision) *
        CASE
            WHEN side::text = 'BUY'::text THEN 1
            ELSE '-1'::integer
        END::double precision) > 0::double precision OR outcome = 'L'::text AND ((roi_target / entry::double precision - 1::double precision) *
        CASE
            WHEN side::text = 'BUY'::text THEN 1
            ELSE '-1'::integer
        END::double precision) < 0::double precision);
REVOKE ALL ON autotrade_analytics.manual_dashboard_trade_results FROM PUBLIC,anon,authenticated;

-- Confirm unique, fully reconciled historical entries:
-- SELECT symbol,count(*),count(DISTINCT entry_order_id)
-- FROM autotrade_analytics.manual_dashboard_trade_results GROUP BY symbol;
