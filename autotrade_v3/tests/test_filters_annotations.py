"""Unit tests for autotrade_v3.external.annotations and autotrade_v3.filters."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from autotrade_v3.external.annotations import AnnotationStore
from autotrade_v3.filters import AnnotationGuardFilter, FilterContext, apply_filters


@dataclass
class _DummySignal:
    signal_id: str
    symbol: str
    combo_id: int
    combo_name: str
    timeframe: str
    side: str
    signal_close_ms: int
    signal_close_dt: str


class TestFiltersAndAnnotations(unittest.TestCase):
    def test_missing_as_of_ms_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            store = AnnotationStore(tmp / "annotations.duckdb")

            bad_json = tmp / "bad.json"
            bad_json.write_text(json.dumps([{"symbol": "BTC-USDT", "verdict": "DROP"}]), encoding="utf-8")
            with self.assertRaises(ValueError):
                store.import_file(bad_json, source="gemini_saved")

            bad_csv = tmp / "bad.csv"
            pd.DataFrame([{"symbol": "BTC-USDT", "as_of_ms": "", "verdict": "DROP"}]).to_csv(bad_csv, index=False)
            with self.assertRaises(ValueError):
                store.import_file(bad_csv, source="gemini_saved")

    def test_as_of_ms_boundary_le_vs_gt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            store = AnnotationStore(tmp / "annotations.duckdb")
            sig_close = 1704067259999

            src_json = tmp / "ann.json"
            src_json.write_text(
                json.dumps(
                    [
                        {"symbol": "BTC-USDT", "as_of_ms": sig_close, "verdict": "KEEP"},
                        {"symbol": "BTC-USDT", "as_of_ms": sig_close + 1, "verdict": "DROP"},
                    ]
                ),
                encoding="utf-8",
            )
            imp = store.import_file(src_json, source="gemini_saved", as_of_verified=False)
            self.assertEqual(imp["imported_count"], 2)
            self.assertTrue(imp["as_of_unverified"])
            self.assertEqual(imp["flag"], "AS_OF_UNVERIFIED")

            # Query at exact signal_close_ms: gets sig_close (<=), drops sig_close + 1
            q = store.query_as_of("BTC-USDT", sig_close, source="gemini_saved")
            self.assertEqual(len(q.records), 1)
            self.assertEqual(q.records[0].as_of_ms, sig_close)
            self.assertEqual(q.records[0].payload["verdict"], "KEEP")
            self.assertEqual(q.dropped_future_count, 1)
            self.assertTrue(q.has_unverified)

            # Query at sig_close - 1: gets 0, drops 2
            q_before = store.query_as_of("BTC-USDT", sig_close - 1, source="gemini_saved")
            self.assertEqual(len(q_before.records), 0)
            self.assertEqual(q_before.dropped_future_count, 2)

    def test_annotation_guard_filter_drops_only_when_causal_veto(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            store = AnnotationStore(tmp / "annotations.duckdb")
            t_sig1 = 1704067259999
            t_sig2 = 1704068159999

            src_json = tmp / "ann.json"
            src_json.write_text(
                json.dumps(
                    [
                        {"symbol": "BTC-USDT", "as_of_ms": t_sig1 - 1000, "verdict": "DROP"},
                        {"symbol": "ETH-USDT", "as_of_ms": t_sig2 + 1, "verdict": "DROP"},
                    ]
                ),
                encoding="utf-8",
            )
            store.import_file(src_json, source="gemini_saved", as_of_verified=True)

            sigs = [
                _DummySignal("S1", "BTC-USDT", 1, "C1", "15m", "BUY", t_sig1, "2024-01-01T00:00:59Z"),
                _DummySignal("S2", "ETH-USDT", 1, "C1", "15m", "BUY", t_sig2, "2024-01-01T00:15:59Z"),
            ]
            ctx = FilterContext(annotation_store=store)
            flt = AnnotationGuardFilter(source="gemini_saved")
            res = apply_filters(sigs, [flt], ctx)

            self.assertEqual([s.signal_id for s in res.kept_signals], ["S2"])
            self.assertEqual(len(res.filtered_decisions), 1)
            self.assertEqual(res.filtered_decisions[0].signal_id, "S1")
            self.assertEqual(res.filtered_decisions[0].status, "FILTERED")
            self.assertEqual(res.filtered_decisions[0].reason, "annotation_guard")
            self.assertEqual(res.dropped_future_annotations, 1)
            self.assertFalse(res.used_unverified_annotations)
