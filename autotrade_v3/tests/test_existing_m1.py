import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import duckdb

from autotrade_v3.data_backtest.accept_existing import inspect

class TestExisting(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"raw";self.root.mkdir()
        self.out=Path(self.tmp.name)/"out"
        self.files=[]
        for sym in ("BTCUSDT","ETHUSDT"):
            f=self.root/(sym+".parquet")
            c=duckdb.connect()
            c.execute(f" COPY (SELECT '{sym}' AS symbol, x AS open_ms, 100 AS open, 101 AS high, 99 AS low, 100 AS close, 2 AS volume FROM unnest([1698796800000::BIGINT,1698796860000::BIGINT]) AS t(x)) TO '{str(f)}' (FORMAT PARQUET) ")
            c.close()
            self.files.append(f)
        self.sm=Path(self.tmp.name)/"sources.json"
        self.sm.write_text(json.dumps({"files":[{"path":f.name,"provider":"BINANCE","market_type":"USDT_PERP"} for f in self.files]}))
        self.hashes=Path(self.tmp.name)/"hashes.json"
        self.hashes.write_text(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in self.files}))
    def test_gap_detected_without_changing_inputs(self):
        r=inspect(self.root,self.out,self.sm,self.hashes,"2023-11-01","2023-11-02")
        self.assertEqual(r["status"],"FAIL")
        self.assertEqual(len(r["metrics"]),2)
        self.assertEqual(r["metrics"][0]["missing"],1438)
        self.assertTrue((self.out/"research.duckdb").exists())
        self.assertEqual(json.loads((self.out/"v3_m1_qa.json").read_text())["gate"],"A")
    def test_checksum_mismatch_blocks(self):
        self.hashes.write_text(json.dumps({f.name:"0"*64 for f in self.files}))
        with self.assertRaisesRegex(ValueError,"checksum"):
            inspect(self.root,self.out,self.sm,self.hashes)
    def test_unknown_provider_blocks(self):
        obj=json.loads(self.sm.read_text())
        obj["files"][0]["provider"]="FAKE"
        self.sm.write_text(json.dumps(obj))
        with self.assertRaisesRegex(ValueError,"provider"):
            inspect(self.root,self.out,self.sm,self.hashes)
