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
        self.sm.write_text(json.dumps({"files":[{"path":f.name,"provider":"BINANCE","market_type":"USDT_PERP","price_type":"LAST"} for f in self.files]}))
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

    def test_hybrid_complete_with_bingx_supplement_and_binance_incomplete(self):
        # 1439 Binance minutes + BingX replacement minute, for both symbols.
        original=list(self.files)
        for f in original:
            c=duckdb.connect()
            c.execute(f"""COPY (
              SELECT '{f.stem}' AS symbol,
                     1698796800000::BIGINT + i * 60000::BIGINT AS open_ms,
                     100 AS open, 101 AS high, 99 AS low, 100 AS close, 2 AS volume
              FROM range(1439) AS t(i)
            ) TO '{str(f)}' (FORMAT PARQUET)""")
            c.close()
            supplement=self.root/(f.stem+"-BINGX.parquet")
            c=duckdb.connect()
            c.execute(f"""COPY (
              SELECT '{f.stem}' AS symbol, 1698796800000::BIGINT + 1439 * 60000::BIGINT AS open_ms,
                     100 AS open, 101 AS high, 99 AS low, 100 AS close, 2 AS volume
            ) TO '{str(supplement)}' (FORMAT PARQUET)""")
            c.close()
            self.files.append(supplement)
        self.sm.write_text(json.dumps({"files":[
          {"path":f.name,"provider":"BINGX" if "-BINGX" in f.name else "BINANCE",
           "market_type":"USDT_PERP","price_type":"LAST"}
          for f in self.files]}))
        self.hashes.write_text(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest()
                                           for f in self.files}))
        r=inspect(self.root,self.out,self.sm,self.hashes,"2023-11-01","2023-11-02")
        self.assertEqual(r["hybrid_gate"],"PASS")
        self.assertEqual(r["binance_source_gate"],"INCOMPLETE")
        self.assertEqual(r["status"],"HYBRID_PASS_SOURCE_INCOMPLETE")
        self.assertTrue(r["research_ready"])
        self.assertEqual(len(r["monthly"]),2)
        self.assertEqual(r["monthly"][0]["bingx_supplement_unique"],1)
        self.assertEqual(r["monthly"][1]["binance_rows"],1439)

    def test_explicit_file_provider_conflict_is_not_silently_relabelled(self):
        f=self.files[0]
        c=duckdb.connect()
        c.execute(f"""COPY (
          SELECT '{f.stem}' AS symbol, x AS open_ms, 100 AS open, 101 AS high,
                 99 AS low, 100 AS close, 2 AS volume, 'BINGX' AS provider
          FROM unnest([1698796800000::BIGINT,1698796860000::BIGINT]) AS t(x)
        ) TO '{str(f)}' (FORMAT PARQUET)""")
        c.close()
        self.hashes.write_text(json.dumps({
            f.name:hashlib.sha256(f.read_bytes()).hexdigest()
            for f in self.files}))
        r=inspect(self.root,self.out,self.sm,self.hashes,"2023-11-01","2023-11-02")
        self.assertEqual(r["hybrid_gate"],"FAIL")
        self.assertEqual(r["metrics"][0]["bad_provenance"],2)

    def test_mixed_file_requires_candle_level_source(self):
        self.sm.write_text(json.dumps({"files":[
          {"path":f.name,"provider":"MIXED","market_type":"USDT_PERP",
           "price_type":"LAST"} for f in self.files]}))
        with self.assertRaisesRegex(ValueError,"per-row venue"):
            inspect(self.root,self.out,self.sm,self.hashes,"2023-11-01","2023-11-02")

    def test_invalid_candle_price_basis_rejected(self):
        f=self.files[0]
        c=duckdb.connect()
        c.execute(f"""COPY (
          SELECT '{f.stem}' AS symbol, x AS open_ms, 100 AS open, 101 AS high,
                 99 AS low, 100 AS close, 2 AS volume, 'binance' AS provider,
                 'USD-M perpetual' AS market, 'mark' AS price_basis
          FROM unnest([1698796800000::BIGINT,1698796860000::BIGINT]) AS t(x)
        ) TO '{str(f)}' (FORMAT PARQUET)""")
        c.close()
        self.hashes.write_text(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest()
                                           for f in self.files}))
        r=inspect(self.root,self.out,self.sm,self.hashes,"2023-11-01","2023-11-02")
        self.assertEqual(r["metrics"][0]["bad_provenance"],2)
        self.assertEqual(r["hybrid_gate"],"FAIL")

    def test_invalid_candle_market_rejected(self):
        f=self.files[0]
        c=duckdb.connect()
        c.execute(f"""COPY (
          SELECT '{f.stem}' AS symbol, x AS open_ms, 100 AS open, 101 AS high,
                 99 AS low, 100 AS close, 2 AS volume, 'binance' AS provider,
                 'SPOT' AS market, 'last' AS price_basis
          FROM unnest([1698796800000::BIGINT,1698796860000::BIGINT]) AS t(x)
        ) TO '{str(f)}' (FORMAT PARQUET)""")
        c.close()
        self.hashes.write_text(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest()
                                           for f in self.files}))
        r=inspect(self.root,self.out,self.sm,self.hashes,"2023-11-01","2023-11-02")
        self.assertEqual(r["metrics"][0]["bad_provenance"],2)
