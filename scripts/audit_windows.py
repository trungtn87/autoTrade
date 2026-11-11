"""Verify exact candle counts for Audit Windows."""
from datetime import datetime, timezone
import duckdb

def to_utc_ms(dt_str: str) -> int:
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)

con = duckdb.connect("catalog/research.duckdb", read_only=True)

windows = [
    ("1_MONTH", "2026-10-01T00:00:00", "2026-11-01T00:00:00", 89280),
    ("6_MONTHS", "2026-05-01T00:00:00", "2026-11-01T00:00:00", 529920),
    ("3_YEARS", "2023-11-01T00:00:00", "2026-11-01T00:00:00", 3156480),
]

for name, s, e, exp in windows:
    s_ms = to_utc_ms(s)
    e_ms = to_utc_ms(e)
    
    cnt_btc = con.execute("SELECT count(*) FROM v3_m1 WHERE symbol='BTCUSDT' AND open_ms >= ? AND open_ms < ?", [s_ms, e_ms]).fetchone()[0]
    cnt_eth = con.execute("SELECT count(*) FROM v3_m1 WHERE symbol='ETHUSDT' AND open_ms >= ? AND open_ms < ?", [s_ms, e_ms]).fetchone()[0]
    total = cnt_btc + cnt_eth
    print(f"{name}: BTC={cnt_btc}, ETH={cnt_eth}, Total={total}, Expected={exp}, Match={total == exp}")

con.close()
