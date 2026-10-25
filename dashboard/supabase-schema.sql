CREATE SCHEMA IF NOT EXISTS autotrade_analytics;

REVOKE ALL ON SCHEMA autotrade_analytics FROM PUBLIC, anon, authenticated;


CREATE TABLE IF NOT EXISTS autotrade_analytics.bingx_fills (
	trade_id VARCHAR(90) NOT NULL, 
	order_id VARCHAR(90) NOT NULL, 
	symbol VARCHAR(40) NOT NULL, 
	side VARCHAR(12) NOT NULL, 
	position_side VARCHAR(12) NOT NULL, 
	qty FLOAT NOT NULL, 
	price FLOAT NOT NULL, 
	realized_pnl FLOAT NOT NULL, 
	fee FLOAT NOT NULL, 
	timestamp_ms BIGINT NOT NULL, 
	raw JSON NOT NULL, 
	PRIMARY KEY (trade_id, symbol)
)

;

ALTER TABLE autotrade_analytics.bingx_fills ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON autotrade_analytics.bingx_fills FROM PUBLIC, anon, authenticated;

CREATE INDEX IF NOT EXISTS ix_autotrade_analytics_bingx_fills_symbol ON autotrade_analytics.bingx_fills (symbol);

CREATE INDEX IF NOT EXISTS ix_autotrade_analytics_bingx_fills_timestamp_ms ON autotrade_analytics.bingx_fills (timestamp_ms);

CREATE INDEX IF NOT EXISTS ix_autotrade_analytics_bingx_fills_order_id ON autotrade_analytics.bingx_fills (order_id);


CREATE TABLE IF NOT EXISTS autotrade_analytics.bingx_force_orders (
	force_key VARCHAR(120) NOT NULL, 
	order_id VARCHAR(90) NOT NULL, 
	symbol VARCHAR(40) NOT NULL, 
	reason VARCHAR(50) NOT NULL, 
	timestamp_ms BIGINT NOT NULL, 
	raw JSON NOT NULL, 
	PRIMARY KEY (force_key)
)

;

ALTER TABLE autotrade_analytics.bingx_force_orders ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON autotrade_analytics.bingx_force_orders FROM PUBLIC, anon, authenticated;


CREATE TABLE IF NOT EXISTS autotrade_analytics.bingx_income (
	income_key VARCHAR(180) NOT NULL, 
	symbol VARCHAR(40) NOT NULL, 
	income_type VARCHAR(45) NOT NULL, 
	amount FLOAT NOT NULL, 
	asset VARCHAR(15) NOT NULL, 
	timestamp_ms BIGINT NOT NULL, 
	trade_id VARCHAR(90) NOT NULL, 
	raw JSON NOT NULL, 
	PRIMARY KEY (income_key)
)

;

ALTER TABLE autotrade_analytics.bingx_income ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON autotrade_analytics.bingx_income FROM PUBLIC, anon, authenticated;

CREATE INDEX IF NOT EXISTS ix_autotrade_analytics_bingx_income_income_type ON autotrade_analytics.bingx_income (income_type);


CREATE TABLE IF NOT EXISTS autotrade_analytics.bingx_orders (
	order_id VARCHAR(90) NOT NULL, 
	symbol VARCHAR(40) NOT NULL, 
	side VARCHAR(12) NOT NULL, 
	position_side VARCHAR(12) NOT NULL, 
	type VARCHAR(45) NOT NULL, 
	status VARCHAR(40) NOT NULL, 
	price FLOAT NOT NULL, 
	avg_price FLOAT NOT NULL, 
	quantity FLOAT NOT NULL, 
	filled_qty FLOAT NOT NULL, 
	created_ms BIGINT NOT NULL, 
	updated_ms BIGINT NOT NULL, 
	raw JSON NOT NULL, 
	PRIMARY KEY (order_id)
)

;

ALTER TABLE autotrade_analytics.bingx_orders ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON autotrade_analytics.bingx_orders FROM PUBLIC, anon, authenticated;

CREATE INDEX IF NOT EXISTS ix_autotrade_analytics_bingx_orders_symbol ON autotrade_analytics.bingx_orders (symbol);


CREATE TABLE IF NOT EXISTS autotrade_analytics.bingx_position_history (
	position_key VARCHAR(120) NOT NULL, 
	symbol VARCHAR(40) NOT NULL, 
	timestamp_ms BIGINT NOT NULL, 
	raw JSON NOT NULL, 
	PRIMARY KEY (position_key)
)

;

ALTER TABLE autotrade_analytics.bingx_position_history ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON autotrade_analytics.bingx_position_history FROM PUBLIC, anon, authenticated;


CREATE TABLE IF NOT EXISTS autotrade_analytics.signal_links (
	entry_order_id VARCHAR(90) NOT NULL, 
	combo VARCHAR(100) NOT NULL, 
	signal_id VARCHAR(120) NOT NULL, 
	symbol VARCHAR(40) NOT NULL, 
	side VARCHAR(12) NOT NULL, 
	signal_time_ms BIGINT NOT NULL, 
	source VARCHAR(30) NOT NULL, 
	PRIMARY KEY (entry_order_id)
)

;

ALTER TABLE autotrade_analytics.signal_links ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON autotrade_analytics.signal_links FROM PUBLIC, anon, authenticated;

CREATE INDEX IF NOT EXISTS ix_autotrade_analytics_signal_links_combo ON autotrade_analytics.signal_links (combo);


CREATE TABLE IF NOT EXISTS autotrade_analytics.sync_runs (
	id SERIAL NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(20) NOT NULL, 
	details TEXT NOT NULL, 
	PRIMARY KEY (id)
)

;

ALTER TABLE autotrade_analytics.sync_runs ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON autotrade_analytics.sync_runs FROM PUBLIC, anon, authenticated;
