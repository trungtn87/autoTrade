"""Offline regression for the Binance USD-M M1 -> ETH M15 warm-up importer."""
import unittest
from datetime import datetime, timezone
from scripts.import_v3_eth_m15_binance_warmup import MINUTE, STEP, aggregate_month, check_overlap, strip_verified_header


def month_rows():
    start=int(datetime(2026,7,1,tzinfo=timezone.utc).timestamp()*1000)
    return [[str(start+i*MINUTE),"100","110","90","105","2",str(start+(i+1)*MINUTE-1)]
            for i in range(44640)]


class SeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=month_rows()

    def test_exact_month_m15(self):
        out=aggregate_month(self.rows,"2026-07")
        self.assertEqual(len(out),2976)
        self.assertEqual(out[0][1:6],(100.0,110.0,90.0,105.0,30.0))
        self.assertEqual(out[0][-1]-out[0][0],STEP-1)

    def test_known_binance_header_is_skipped_only_once(self):
        header=["open_time","open","high","low","close","volume","close_time",
                "quote_asset_volume","number_of_trades","taker_buy_base_asset_volume",
                "taker_buy_quote_asset_volume","ignore"]
        self.assertEqual(strip_verified_header([header,*self.rows]),self.rows)
        candles=aggregate_month(strip_verified_header([header,*self.rows]),"2026-07")
        self.assertEqual(len(candles),2976)

    def test_unknown_header_and_duplicate_header_are_rejected(self):
        header=["created_at","open","high","low","close","volume","close_time"]
        with self.assertRaisesRegex(RuntimeError,"Unexpected Binance CSV header"):
            strip_verified_header([header,*self.rows])
        valid=["open_time","open","high","low","close","volume","close_time"]
        with self.assertRaisesRegex(RuntimeError,"Invalid futures M1 values"):
            aggregate_month(strip_verified_header([valid,valid,*self.rows]),"2026-07")

    def test_missing_m1_fails(self):
        with self.assertRaisesRegex(RuntimeError,"incomplete"):
            aggregate_month(self.rows[:-1],"2026-07")

    def test_timestamp_hole_fails(self):
        modified=[x[:] for x in self.rows]
        modified[20][0]=str(int(modified[20][0])+MINUTE)
        with self.assertRaisesRegex(RuntimeError,"Invalid futures M1 time"):
            aggregate_month(modified,"2026-07")

    def test_invalid_ohlcv_fails(self):
        modified=[x[:] for x in self.rows]
        modified[21][2]="89"
        with self.assertRaisesRegex(RuntimeError,"Invalid futures M1 OHLCV"):
            aggregate_month(modified,"2026-07")

    def test_microsecond_timestamp_fails(self):
        modified=[x[:] for x in self.rows]
        modified[21][0]+="000"
        with self.assertRaisesRegex(RuntimeError,"Invalid futures M1 time"):
            aggregate_month(modified,"2026-07")

    def test_overlap_success_and_failure(self):
        first=int(datetime(2026,7,1,tzinfo=timezone.utc).timestamp()*1000)
        history={first+i*STEP:(first+i*STEP,100,110,90,105,30,first+(i+1)*STEP-1) for i in range(96)}

        class Cur:
            def __init__(self,close): self.close=close
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def execute(self,*args): pass
            def fetchall(self): return [(first+i*STEP,self.close) for i in range(96)]

        class DB:
            def __init__(self,close): self.close=close
            def cursor(self): return Cur(self.close)

        self.assertEqual(check_overlap(DB(105.0),history,first)["max_close_diff_pct"],0)
        with self.assertRaisesRegex(RuntimeError,"divergence"):
            check_overlap(DB(80.0),history,first)


if __name__ == "__main__":
    unittest.main()
