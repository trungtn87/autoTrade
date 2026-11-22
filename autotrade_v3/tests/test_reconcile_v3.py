"""Kiểm thử toàn diện cho công cụ đối soát V3-17a (autotrade_v3.reconcile_v3)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from autotrade_v3.collector_v3.models import sha256_file
from autotrade_v3.reconcile_v3.eras import ERA_1_50TP_50TRAIL, ERA_2_FIXED_TP_SL
from autotrade_v3.reconcile_v3.keys import (
    ComboVerificationError,
    KeyMappingError,
    event_id_to_signal_id,
    signal_id_to_event_id,
    verify_live_signals_against_combos,
)
from autotrade_v3.reconcile_v3.labels_vi import find_english_label_leaks
from autotrade_v3.reconcile_v3.loader import ManifestVerificationError, load_local_store
from autotrade_v3.reconcile_v3.verify import verify_report
from scripts.run_v3_17a_reconcile import run_reconcile_pipeline

REAL_DATA_ROOT = Path("D:/AutoTrade/data/bingx")


def test_key_mapping_roundtrip_and_invalid() -> None:
    eid = "BTC-USDT|1h|C4|SELL|1794625199999"
    sid = event_id_to_signal_id(eid)
    assert sid == "SIG_BTCUSDT_C4_1h_SELL_1794625199999"
    assert signal_id_to_event_id(sid) == eid

    eid_eth = "ETH-USDT|15m|C10|BUY|1795046399999"
    assert signal_id_to_event_id(event_id_to_signal_id(eid_eth)) == eid_eth

    with pytest.raises(KeyMappingError):
        event_id_to_signal_id("INVALID_KEY")
    with pytest.raises(KeyMappingError):
        signal_id_to_event_id("BTCUSDT_C4_1h_SELL_123")


def test_combo_verification_rejects_unmapped_and_mismatch() -> None:
    valid = [
        {
            "event_id": "BTC-USDT|1h|C4|SELL|1794625199999",
            "symbol": "BTC-USDT",
            "combo": "C4",
            "side": "SELL",
            "entry": "67655.5",
            "tp": "66437.701",
            "sl": "68264.3995",
        }
    ]
    res = verify_live_signals_against_combos(valid)
    assert "BTC-USDT|1h|C4|SELL|1794625199999" in res

    unmapped = [
        {
            "event_id": "BTC-USDT|1h|C99|SELL|1794625199999",
            "symbol": "BTC-USDT",
            "combo": "C99",
            "side": "SELL",
            "entry": "67655.5",
            "tp": "66437.701",
            "sl": "68264.3995",
        }
    ]
    with pytest.raises(ComboVerificationError):
        verify_live_signals_against_combos(unmapped)

    bad_tp = [
        {
            "event_id": "BTC-USDT|1h|C4|SELL|1794625199999",
            "symbol": "BTC-USDT",
            "combo": "C4",
            "side": "SELL",
            "entry": "67655.5",
            "tp": "60000.0",
            "sl": "68264.3995",
        }
    ]
    with pytest.raises(ComboVerificationError):
        verify_live_signals_against_combos(bad_tp)


def test_manifest_sha256_tamper_fails(tmp_path: Path) -> None:
    root = tmp_path / "bingx"
    (root / "manifests").mkdir(parents=True)
    (root / "live_events").mkdir(parents=True)

    pq_path = root / "live_events" / "strategy_signals.parquet"
    tbl = pa.table({"event_id": ["BTC-USDT|1h|C4|SELL|1794625199999"]})
    pq.write_table(tbl, pq_path)
    good_sha = sha256_file(pq_path)

    manifest = {
        "run_id": "test_run",
        "sources": {
            "live_events_v3": {
                "parquet_files": [
                    {"path": "live_events/strategy_signals.parquet", "sha256": good_sha, "row_count": 1}
                ]
            }
        },
    }
    (root / "manifests" / "latest_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # Sửa nội dung parquet để giả lập sai SHA-256
    pq.write_table(pa.table({"event_id": ["BTC-USDT|1h|C4|SELL|1794625199998"]}), pq_path)
    with pytest.raises(ManifestVerificationError):
        load_local_store(root)


def test_mutation_delete_order_and_modify_price_fails_verify() -> None:
    if not (REAL_DATA_ROOT / "manifests" / "latest_manifest.json").is_file():
        pytest.skip("Không có kho dữ liệu local D:/AutoTrade/data/bingx")

    # Đọc trực tiếp artifact đã sinh hoặc chạy vào biến tạm
    evidence_json = Path("D:/AutoTrade/evidence/v3-17a/reconcile_report.json")
    if not evidence_json.is_file():
        pytest.skip("Chưa có evidence/v3-17a/reconcile_report.json")

    payload = json.loads(evidence_json.read_text(encoding="utf-8"))
    assert verify_report(payload).passed is True

    # Mutation 1: xóa một lệnh live trong bảng orders mà giữ nguyên summary -> verify phải FAIL
    mutated_del = copy.deepcopy(payload)
    mutated_del["orders"].pop()
    vr_del = verify_report(mutated_del)
    assert vr_del.passed is False
    assert vr_del.errors

    # Mutation 2: sửa một giá/PnL trong bảng orders -> verify phải FAIL
    mutated_px = copy.deepcopy(payload)
    mutated_px["orders"][0]["items"]["pnl"]["live_net_pnl"] = "999.99"
    vr_px = verify_report(mutated_px)
    assert vr_px.passed is False
    assert vr_px.errors


def test_real_store_end_to_end_and_reproducibility(tmp_path: Path) -> None:
    if not (REAL_DATA_ROOT / "manifests" / "latest_manifest.json").is_file():
        pytest.skip("Không có kho dữ liệu local D:/AutoTrade/data/bingx")

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    p1, html1 = run_reconcile_pipeline(data_root=REAL_DATA_ROOT, output_dir=out1, repo_root=Path("D:/AutoTrade"))
    p2, html2 = run_reconcile_pipeline(data_root=REAL_DATA_ROOT, output_dir=out2, repo_root=Path("D:/AutoTrade"))

    # Tái lập: cùng dữ liệu -> cùng hash
    assert p1["report_sha256"] == p2["report_sha256"]
    assert html1 == html2

    # Không rò rỉ nhãn tiếng Anh
    assert find_english_label_leaks(html1) == []

    # Kiểm tra quy mô mẫu & phân tách kỷ nguyên
    assert p1["sample_governance"]["total_orders"] == 22
    assert p1["sample_governance"]["closed_orders"] == 22
    assert p1["sample_governance"]["is_small_sample"] is True
    assert "chưa kết luận" in p1["sample_governance"]["warning_vi"]

    assert p1["era_summaries"][ERA_1_50TP_50TRAIL]["orders_count"] == 18
    assert p1["era_summaries"][ERA_2_FIXED_TP_SL]["orders_count"] == 4

    # Funding chỉ ESTIMATED và không cộng vào PnL thực
    for row in p1["orders"]:
        fu = row["items"]["funding"]
        assert fu["provenance"] == "ESTIMATED"
        assert fu["live_db_funding"] is None
        assert fu["included_in_live_net_pnl"] is False

    # Verify pass
    assert verify_report(p1, html1).passed is True
