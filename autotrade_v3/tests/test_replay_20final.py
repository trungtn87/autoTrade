"""V3-02 offline no-credentials boundary, closed candle time and B1 comparator."""
import hashlib
import tempfile
import unittest
from pathlib import Path
import duckdb
import pandas as pd
from autotrade_v3.data_backtest.replay_20final import digest,row_event,compare_b1,m15_from_catalog,replay_symbol

class ReplayTests(unittest.TestCase):
    def test_frozen_digest_encoding(self):
        self.assertEqual(digest([[0,"L",100,1,False]]),
                         hashlib.sha256(b'[[0,"L",100,1,false]]').hexdigest())
    def test_h1_signal_only_on_60m_close(self):
        idx=pd.date_range("2026-10-01",periods=4,freq="15min",tz="UTC")
        df=pd.DataFrame({"provider":["BINANCE"]*4},index=idx)
        row=row_event("BTC-USDT",101,"C11","1h","L",idx[0],3,1,False,True,df)
        self.assertEqual(row["signal_close_ms"],
                         pd.Timestamp("2026-10-01T00:59:59.999Z").value//1_000_000)
        with self.assertRaises(AssertionError):
            row_event("BTC-USDT",101,"C11","1h","L",idx[0],2,1,False,True,df)
    def test_same_input_parity_detects_single_change(self):
        base={"results":{s:{"C1":dict(raw_events=1,passed_events=1,raw_sha256="a",passed_sha256="b")} for s in ("BTC-USDT","ETH-USDT")}}
        self.assertEqual(compare_b1(base,base["results"])["status"],"PASS")
        changed={s:{"C1":dict(base["results"][s]["C1"])} for s in ("BTC-USDT","ETH-USDT")}
        changed["BTC-USDT"]["C1"]["passed_events"]=0
        self.assertEqual(compare_b1(base,changed)["status"],"FAIL")
    def test_gap_fails_before_strategy_replay(self):
        idx=pd.date_range("2026-01-01",periods=3400,freq="15min",tz="UTC").delete(2)
        with self.assertRaisesRegex(ValueError,"gapped"):
            replay_symbol("BTC-USDT",pd.DataFrame(index=idx))
    def test_incomplete_m1_cannot_be_resampled(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/"data.duckdb";con=duckdb.connect(str(db))
            con.execute("""CREATE TABLE v3_m1 AS SELECT 'BTCUSDT' AS symbol,
                1790812800000::BIGINT+i*60000::BIGINT AS open_ms,
                1.0 AS open,2.0 AS high,0.5 AS low,1.5 AS "close",
                1.0 AS volume,'BINANCE' AS provider FROM range(14) x(i)""")
            con.close()
            with self.assertRaisesRegex(ValueError,"Incomplete 15-minute"):
                m15_from_catalog(db,"BTC-USDT","2026-10-01","2026-10-02")
if __name__=="__main__":unittest.main()
