"""Kiểm thử toàn diện cho công cụ đối soát V3-17a bước 2 (autotrade_v3.reconcile_v3)."""
from __future__ import annotations

import copy
from decimal import Decimal
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
from autotrade_v3.reconcile_v3.reconcile_orders import compute_entry_slippage_bps
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


def test_slippage_sign_buy_and_sell() -> None:
    # BUY: khớp cao hơn tham chiếu -> + (bất lợi), thấp hơn -> - (có lợi)
    buy_adv = compute_entry_slippage_bps("BUY", Decimal("1000"), Decimal("1002"))
    buy_fav = compute_entry_slippage_bps("BUY", Decimal("1000"), Decimal("998"))
    assert buy_adv == Decimal("20")
    assert buy_fav == Decimal("-20")

    # SELL: khớp thấp hơn tham chiếu -> + (bất lợi), cao hơn -> - (có lợi)
    sell_adv = compute_entry_slippage_bps("SELL", Decimal("1000"), Decimal("998"))
    sell_fav = compute_entry_slippage_bps("SELL", Decimal("1000"), Decimal("1002"))
    assert sell_adv == Decimal("20")
    assert sell_fav == Decimal("-20")


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
    pq.write_table(pa.table({"event_id": ["BTC-USDT|1h|C4|SELL|1794625199999"]}), pq_path)
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

    pq.write_table(pa.table({"event_id": ["BTC-USDT|1h|C4|SELL|1794625199998"]}), pq_path)
    with pytest.raises(ManifestVerificationError):
        load_local_store(root)


def test_mutation_delete_order_price_and_label_fails_verify(tmp_path: Path) -> None:
    if not (REAL_DATA_ROOT / "manifests" / "latest_manifest.json").is_file():
        pytest.skip("Không có kho dữ liệu local D:/AutoTrade/data/bingx")

    payload, html_text = run_reconcile_pipeline(
        data_root=REAL_DATA_ROOT,
        output_dir=tmp_path / "mut",
        repo_root=Path("D:/AutoTrade"),
    )
    assert verify_report(payload, html_text).passed is True

    # Mutation 1: xóa một lệnh trong bảng orders -> verify FAIL
    mutated_del = copy.deepcopy(payload)
    mutated_del["orders"].pop()
    vr_del = verify_report(mutated_del)
    assert vr_del.passed is False
    assert vr_del.errors

    # Mutation 2: sửa một giá/PnL trong bảng orders -> verify FAIL
    mutated_px = copy.deepcopy(payload)
    mutated_px["orders"][0]["items"]["pnl"]["live_net_pnl"] = "999.99"
    vr_px = verify_report(mutated_px)
    assert vr_px.passed is False
    assert vr_px.errors

    # Mutation 3: đổi nhãn nguyên nhân (root_cause) -> verify FAIL
    mutated_lbl = copy.deepcopy(payload)
    mutated_lbl["orders"][0]["root_cause"] = "HỆ_THỐNG"
    vr_lbl = verify_report(mutated_lbl)
    assert vr_lbl.passed is False
    assert vr_lbl.errors


def test_real_store_step2_classifications_and_compare_from(tmp_path: Path) -> None:
    if not (REAL_DATA_ROOT / "manifests" / "latest_manifest.json").is_file():
        pytest.skip("Không có kho dữ liệu local D:/AutoTrade/data/bingx")

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    p1, html1 = run_reconcile_pipeline(data_root=REAL_DATA_ROOT, output_dir=out1, repo_root=Path("D:/AutoTrade"))
    p2, html2 = run_reconcile_pipeline(data_root=REAL_DATA_ROOT, output_dir=out2, repo_root=Path("D:/AutoTrade"))

    # Tái lập & không rò rỉ tiếng Anh
    assert p1["report_sha256"] == p2["report_sha256"]
    assert html1 == html2
    assert find_english_label_leaks(html1) == []

    # compare_from = 2026-11-22T02:30:33Z -> chưa có lệnh sau mốc
    assert p1["compare_from_utc"] == "2026-11-22T02:30:33Z"
    assert p1["sample_governance"]["closed_orders"] == 0
    assert p1["sample_governance"]["has_orders_since_compare_from"] is False
    assert p1["sample_governance"]["empty_state_label_vi"] == "CHƯA CÓ LỆNH SAU MỐC SO SÁNH"
    assert "CHƯA CÓ LỆNH SAU MỐC SO SÁNH" in html1

    # Khối lịch sử giữ đủ 22 lệnh tách kỷ nguyên
    hist = p1["historical"]
    assert hist["total_orders"] == 22
    assert hist["era_summaries"][ERA_1_50TP_50TRAIL]["orders_count"] == 18
    assert hist["era_summaries"][ERA_2_FIXED_TP_SL]["orders_count"] == 4
    assert hist["system_issues"] == []

    # Kiểm tra 3 sửa phân loại trên dữ liệu thật
    by_eid = {r["event_id"]: r for r in p1["orders"]}
    # 1. Vị thế gộp ETH C13 & C15 -> UNRESOLVED + VỊ_THẾ_GỘP_KHÔNG_PHÂN_BỔ_ĐƯỢC
    for eid in ("ETH-USDT|1h|C13|SELL|1794697199999", "ETH-USDT|1h|C15|SELL|1794697199999"):
        assert by_eid[eid]["overall_provenance"] == "UNRESOLVED"
        assert by_eid[eid]["root_cause"] == "VỊ_THẾ_GỘP_KHÔNG_PHÂN_BỔ_ĐƯỢC"

    # 2. TP/SL từ avg_price (ETH C4) -> MÔ_HÌNH_THỰC_THI
    assert by_eid["ETH-USDT|1h|C4|SELL|1795125599999"]["root_cause"] == "MÔ_HÌNH_THỰC_THI"

    # 3. Trượt giá có lợi lớn (BTC TIER SELL -28.63 bps) -> MÔ_HÌNH_THỰC_THI + ngoại lệ lớn + độ trễ
    tier = by_eid["BTC-USDT|1h|TIER|SELL|1795237199999"]
    assert tier["root_cause"] == "MÔ_HÌNH_THỰC_THI"
    assert tier["is_large_favorable_outlier"] is True
    assert tier["entry_latency_sec"] is not None

    # Kiểm tra đối soát tín hiệu theo kỷ nguyên & NEW6_VIRTUAL_POSITION
    eras = {eb["era_id"]: eb for eb in p1["signal_reconciliation"]["era_breakdown"]}
    assert eras["SIGNAL_ERA_20FINAL"]["engine_status"] == "AVAILABLE"
    assert eras["SIGNAL_ERA_20FINAL"]["new6_virtual_position_count"] > 0
    assert eras["SIGNAL_ERA_V3_LIVE_23_LAB_10_V1"]["engine_status"] == "KHÔNG_DỰNG_ĐƯỢC_ENGINE"

    assert verify_report(p1, html1).passed is True
