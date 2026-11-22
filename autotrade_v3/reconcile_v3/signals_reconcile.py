"""Đối soát tín hiệu Lab vs Live theo đúng engine của từng kỷ nguyên (bước 3)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.reconcile_v3.era_engines import generate_signals_for_era
from autotrade_v3.reconcile_v3.eras import VpsOffWindow, is_in_vps_off_window
from autotrade_v3.reconcile_v3.keys import build_combo_table, parse_event_id

DEFAULT_LIVE_START_MS = 1794614400000  # 2026-11-14T00:00:00Z


def _iso_ms(ts_str: str) -> int:
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def build_signal_engine_eras(deploy_23_finalized_utc: str | None = None) -> tuple[dict[str, Any], ...]:
    """Xây dựng danh sách kỷ nguyên engine tín hiệu; kỷ nguyên thứ 6 dùng deploy_23_finalized_utc."""
    return (
        {
            "era_id": "SIGNAL_ERA_20FINAL",
            "label_vi": "20FINAL (14/11 – trước PR #129 16/11 15:06:50 UTC)",
            "start_utc": "2026-11-14T00:00:00Z",
            "end_utc": "2026-11-16T15:06:50Z",
            "engine_ref": "20FINAL@1",
            "engine_status": "AVAILABLE",
        },
        {
            "era_id": "SIGNAL_ERA_V3_LIVE_20_POSITIVE_FIXED_V1",
            "label_vi": "V3_LIVE_20_POSITIVE_FIXED_V1 (PR #129: 16/11 15:06:50 – 19/11 12:47:21 UTC)",
            "start_utc": "2026-11-16T15:06:50Z",
            "end_utc": "2026-11-19T12:47:21Z",
            "engine_ref": "20FINAL_FIXED_TPSL_ETH_C1_H1_BOTH_1@1.0.0",
            "engine_status": "AVAILABLE",
        },
        {
            "era_id": "SIGNAL_ERA_V3_LIVE_23_LAB_5_V1",
            "label_vi": "V3_LIVE_23_LAB_5_V1 (PR #148: 19/11 12:47:21 – 20/11 03:16:27 UTC)",
            "start_utc": "2026-11-19T12:47:21Z",
            "end_utc": "2026-11-20T03:16:27Z",
            "engine_ref": "V3_LAB_5@5.0.0 (23 mã)",
            "engine_status": "AVAILABLE",
        },
        {
            "era_id": "SIGNAL_ERA_V3_LIVE_23_LAB_8_V1",
            "label_vi": "V3_LIVE_23_LAB_8_V1 (PR #159: 20/11 03:16:27 – 20/11 04:35:34 UTC)",
            "start_utc": "2026-11-20T03:16:27Z",
            "end_utc": "2026-11-20T04:35:34Z",
            "engine_ref": "V3_LAB_8@8.0.0 (23 mã)",
            "engine_status": "AVAILABLE",
        },
        {
            "era_id": "SIGNAL_ERA_V3_LIVE_23_LAB_10_V1",
            "label_vi": "V3_LIVE_23_LAB_10_V1 (PR #160+: từ 20/11 04:35:34 UTC)",
            "start_utc": "2026-11-20T04:35:34Z",
            "end_utc": deploy_23_finalized_utc,
            "engine_ref": "V3_LAB_10@e6422fd (23 mã)",
            "engine_status": "AVAILABLE",
        },
        {
            "era_id": "SIGNAL_ERA_23_FINALIZED_LAB11_PRUNE23",
            "label_vi": "23 mã chốt V3_LAB_11_PRUNE23 (chờ deploy)" if not deploy_23_finalized_utc else f"23 mã chốt V3_LAB_11_PRUNE23 (từ {deploy_23_finalized_utc})",
            "start_utc": deploy_23_finalized_utc,
            "end_utc": None,
            "engine_ref": "V3_LAB_11_PRUNE23@11.0.0 (166aafd)",
            "engine_status": "AVAILABLE" if deploy_23_finalized_utc else "CHỜ_DEPLOY",
        },
    )


def classify_signal_engine_era(ts_ms: int, eras: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    for era in eras:
        if not era["start_utc"]:
            continue
        s_ms = _iso_ms(era["start_utc"])
        e_ms = _iso_ms(era["end_utc"]) if era["end_utc"] else None
        if ts_ms >= s_ms and (e_ms is None or ts_ms < e_ms):
            return era
    return eras[0]


@dataclass(frozen=True)
class SignalReconcileItem:
    event_id: str
    signal_id: str
    symbol: str
    timeframe: str
    combo: str
    side: str
    signal_close_ms: int
    signal_close_utc: str
    signal_era_id: str
    status: str  # KHỚP | THIẾU | THỪA | VPS_TẮT | NEW6_VIRTUAL_POSITION | LỆCH_THỜI_GIAN
    reason_code: str
    reason_detail_vi: str
    time_diff_ms: int = 0


@dataclass(frozen=True)
class SignalEraBreakdown:
    era_id: str
    label_vi: str
    engine_ref: str
    engine_status: str
    lab_total: int
    live_total: int
    matched_count: int
    missing_count: int
    extra_count: int
    new6_virtual_position_count: int
    vps_off_count: int
    time_skew_count: int


@dataclass
class SignalReconcileSummary:
    compare_from_utc: str
    compare_from_ms: int
    deploy_23_finalized_utc: str | None = None
    since_compare_from: dict[str, Any] = field(default_factory=dict)
    projected_23_finalized_since_compare_from: dict[str, Any] = field(default_factory=dict)
    era_breakdown: list[SignalEraBreakdown] = field(default_factory=list)
    lab_total_in_window: int = 0
    live_total: int = 0
    matched_count: int = 0
    missing_count: int = 0
    extra_count: int = 0
    new6_virtual_position_count: int = 0
    vps_off_count: int = 0
    time_skew_count: int = 0
    match_rate_vs_live_pct: float = 0.0
    items: list[SignalReconcileItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _ms_to_iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _build_m15_df(candles_15m: list[dict[str, Any]], symbol: str) -> pd.DataFrame:
    rows = sorted((r for r in candles_15m if r.get("symbol") == symbol), key=lambda x: int(x["open_time"]))
    if not rows:
        raise ValueError(f"Không có nến 15m cho {symbol} trong live_events/candles.parquet")
    return pd.DataFrame(
        {
            "open": [float(r["open"]) for r in rows],
            "high": [float(r["high"]) for r in rows],
            "low": [float(r["low"]) for r in rows],
            "close": [float(r["close"]) for r in rows],
            "volume": [float(r["volume"]) for r in rows],
        },
        index=pd.to_datetime([int(r["open_time"]) for r in rows], unit="ms", utc=True),
    )


def _signal_to_event_id(s: Signal) -> str:
    return f"{s.symbol}|{s.timeframe}|{s.combo_name}|{s.side}|{s.signal_close_ms}"


def reconcile_signals(
    *,
    candles_15m: list[dict[str, Any]],
    live_signals: list[dict[str, Any]],
    execution_orders: list[dict[str, Any]],
    execution_results: list[dict[str, Any]],
    vps_off_windows: list[VpsOffWindow],
    compare_from_utc: str = "2026-11-22T02:30:33Z",
    deploy_23_finalized_utc: str | None = None,
    live_start_ms: int = DEFAULT_LIVE_START_MS,
) -> SignalReconcileSummary:
    """Chạy đúng engine Lab cho từng kỷ nguyên trên cùng nến M15 và kiểm tra ngang bằng với Live."""
    _ = execution_orders, execution_results  # reserved for future interval checks
    compare_from_ms = _iso_ms(compare_from_utc)
    eras = build_signal_engine_eras(deploy_23_finalized_utc)
    combo_table = build_combo_table()

    bars_by_sym = {sym: _build_m15_df(candles_15m, sym) for sym in ("BTC-USDT", "ETH-USDT")}
    only_warmup_by_sym = {"BTC-USDT": False, "ETH-USDT": True}
    max_candle_close_ms = max(int(r["close_time"]) for r in candles_15m)
    window_start_ms = live_start_ms
    if live_signals:
        window_start_ms = min(live_start_ms, min(parse_event_id(str(r["event_id"])).close_ms for r in live_signals))

    notes = [
        f"BTC-USDT: live_events/candles.parquet giữ {len(bars_by_sym['BTC-USDT'])} nến 15m (rolling 3400), chạy với only_warmup_ready=False."
    ]

    live_by_eid: dict[str, dict[str, Any]] = {str(r["event_id"]): r for r in live_signals}
    items: list[SignalReconcileItem] = []
    era_breakdown: list[SignalEraBreakdown] = []
    total_lab_in_window = 0

    for era in eras:
        era_id = str(era["era_id"])
        if era["engine_status"] == "CHỜ_DEPLOY":
            era_breakdown.append(
                SignalEraBreakdown(
                    era_id=era_id,
                    label_vi=str(era["label_vi"]),
                    engine_ref=str(era["engine_ref"]),
                    engine_status="CHỜ_DEPLOY",
                    lab_total=0,
                    live_total=0,
                    matched_count=0,
                    missing_count=0,
                    extra_count=0,
                    new6_virtual_position_count=0,
                    vps_off_count=0,
                    time_skew_count=0,
                )
            )
            continue

        s_ms = max(window_start_ms, _iso_ms(str(era["start_utc"])))
        e_ms = _iso_ms(str(era["end_utc"])) if era["end_utc"] else (max_candle_close_ms + 1)

        all_era_sigs = generate_signals_for_era(
            era_id=era_id,
            bars_by_sym=bars_by_sym,
            only_warmup_ready_by_sym=only_warmup_by_sym,
        )
        lab_era_by_eid: dict[str, Signal] = {}
        for sig in all_era_sigs:
            if s_ms <= sig.signal_close_ms < min(e_ms, max_candle_close_ms + 1):
                lab_era_by_eid[_signal_to_event_id(sig)] = sig

        live_era_eids = {
            eid
            for eid in live_by_eid
            if s_ms <= parse_event_id(eid).close_ms < e_ms
        }
        total_lab_in_window += len(lab_era_by_eid)

        matched = set(lab_era_by_eid.keys()) & live_era_eids
        lab_only = sorted(set(lab_era_by_eid.keys()) - live_era_eids)
        live_only = sorted(live_era_eids - set(lab_era_by_eid.keys()))

        skew_lab_used: set[str] = set()
        skew_live_used: set[str] = set()
        era_items: list[SignalReconcileItem] = []

        for le in live_only:
            lp = parse_event_id(le)
            for be in lab_only:
                if be in skew_lab_used:
                    continue
                bp = parse_event_id(be)
                if (lp.symbol, lp.combo_name, lp.side) == (bp.symbol, bp.combo_name, bp.side):
                    diff = lp.close_ms - bp.close_ms
                    if 0 < abs(diff) <= 3_600_000:
                        skew_lab_used.add(be)
                        skew_live_used.add(le)
                        era_items.append(
                            SignalReconcileItem(
                                event_id=le,
                                signal_id=lp.signal_id,
                                symbol=lp.symbol,
                                timeframe=lp.timeframe,
                                combo=lp.combo_name,
                                side=lp.side,
                                signal_close_ms=lp.close_ms,
                                signal_close_utc=_ms_to_iso(lp.close_ms),
                                signal_era_id=era_id,
                                status="LỆCH_THỜI_GIAN",
                                reason_code="CHƯA_GIẢI_THÍCH",
                                reason_detail_vi=f"Lệch {diff // 1000}s so với tín hiệu Lab {be}",
                                time_diff_ms=diff,
                            )
                        )
                        break

        for eid in sorted(matched):
            pk = parse_event_id(eid)
            era_items.append(
                SignalReconcileItem(
                    event_id=eid,
                    signal_id=pk.signal_id,
                    symbol=pk.symbol,
                    timeframe=pk.timeframe,
                    combo=pk.combo_name,
                    side=pk.side,
                    signal_close_ms=pk.close_ms,
                    signal_close_utc=_ms_to_iso(pk.close_ms),
                    signal_era_id=era_id,
                    status="KHỚP",
                    reason_code="KHỚP",
                    reason_detail_vi=f"Khớp chính xác với engine {era['engine_ref']}",
                )
            )

        for eid in lab_only:
            if eid in skew_lab_used:
                continue
            pk = parse_event_id(eid)
            vps_win = is_in_vps_off_window(pk.close_ms, vps_off_windows)
            if vps_win is not None:
                era_items.append(
                    SignalReconcileItem(
                        event_id=eid,
                        signal_id=pk.signal_id,
                        symbol=pk.symbol,
                        timeframe=pk.timeframe,
                        combo=pk.combo_name,
                        side=pk.side,
                        signal_close_ms=pk.close_ms,
                        signal_close_utc=_ms_to_iso(pk.close_ms),
                        signal_era_id=era_id,
                        status="VPS_TẮT",
                        reason_code="VPS_TẮT",
                        reason_detail_vi=f"Nằm trong cửa sổ VPS_TẮT ({vps_win.start_utc} -> {vps_win.end_utc})",
                    )
                )
                continue

            spec = combo_table.get((pk.symbol, pk.combo_name))
            if spec is not None and spec.is_new6:
                era_items.append(
                    SignalReconcileItem(
                        event_id=eid,
                        signal_id=pk.signal_id,
                        symbol=pk.symbol,
                        timeframe=pk.timeframe,
                        combo=pk.combo_name,
                        side=pk.side,
                        signal_close_ms=pk.close_ms,
                        signal_close_utc=_ms_to_iso(pk.close_ms),
                        signal_era_id=era_id,
                        status="NEW6_VIRTUAL_POSITION",
                        reason_code="NEW6_VIRTUAL_POSITION",
                        reason_detail_vi="Lab phát lặp trên các nến 1H theo virtual_position; Live giữ 1 vị thế thực.",
                    )
                )
                continue

            era_items.append(
                SignalReconcileItem(
                    event_id=eid,
                    signal_id=pk.signal_id,
                    symbol=pk.symbol,
                    timeframe=pk.timeframe,
                    combo=pk.combo_name,
                    side=pk.side,
                    signal_close_ms=pk.close_ms,
                    signal_close_utc=_ms_to_iso(pk.close_ms),
                    signal_era_id=era_id,
                    status="THIẾU",
                    reason_code="CHƯA_GIẢI_THÍCH",
                    reason_detail_vi=f"Engine {era['engine_ref']} phát tín hiệu nhưng Live không có",
                )
            )

        for eid in live_only:
            if eid in skew_live_used:
                continue
            pk = parse_event_id(eid)
            reason_code = "WARMUP_WINDOW_3400" if pk.symbol == "BTC-USDT" else "CHƯA_GIẢI_THÍCH"
            reason_detail = (
                "BTC-USDT chỉ có 3400 nến rolling; trạng thái warmup/L2 tại thời điểm live có thể khác"
                if pk.symbol == "BTC-USDT"
                else f"Live có tín hiệu nhưng engine {era['engine_ref']} không phát"
            )
            era_items.append(
                SignalReconcileItem(
                    event_id=eid,
                    signal_id=pk.signal_id,
                    symbol=pk.symbol,
                    timeframe=pk.timeframe,
                    combo=pk.combo_name,
                    side=pk.side,
                    signal_close_ms=pk.close_ms,
                    signal_close_utc=_ms_to_iso(pk.close_ms),
                    signal_era_id=era_id,
                    status="THỪA",
                    reason_code=reason_code,
                    reason_detail_vi=reason_detail,
                )
            )

        items.extend(era_items)
        era_breakdown.append(
            SignalEraBreakdown(
                era_id=era_id,
                label_vi=str(era["label_vi"]),
                engine_ref=str(era["engine_ref"]),
                engine_status="AVAILABLE",
                lab_total=len(lab_era_by_eid),
                live_total=len(live_era_eids),
                matched_count=sum(1 for x in era_items if x.status == "KHỚP"),
                missing_count=sum(1 for x in era_items if x.status == "THIẾU"),
                extra_count=sum(1 for x in era_items if x.status == "THỪA"),
                new6_virtual_position_count=sum(1 for x in era_items if x.status == "NEW6_VIRTUAL_POSITION"),
                vps_off_count=sum(1 for x in era_items if x.status == "VPS_TẮT"),
                time_skew_count=sum(1 for x in era_items if x.status == "LỆCH_THỜI_GIAN"),
            )
        )

    items.sort(key=lambda x: (x.signal_close_ms, x.symbol, x.combo, x.status))

    # Dự kiến tín hiệu của 23 mã chốt (V3_LAB_11_PRUNE23) từ compare_from để theo dõi
    proj_sigs = generate_signals_for_era(
        era_id="SIGNAL_ERA_23_FINALIZED_LAB11_PRUNE23",
        bars_by_sym=bars_by_sym,
        only_warmup_ready_by_sym=only_warmup_by_sym,
    )
    proj_since = [s for s in proj_sigs if s.signal_close_ms >= compare_from_ms]
    projected_23 = {
        "engine_ref": "V3_LAB_11_PRUNE23@11.0.0 (166aafd)",
        "deploy_23_finalized_utc": deploy_23_finalized_utc,
        "deployed": bool(deploy_23_finalized_utc),
        "projected_lab_signals_since_compare_from": len(proj_since),
        "projected_event_ids": [_signal_to_event_id(s) for s in proj_since],
    }

    items_since = [x for x in items if x.signal_close_ms >= compare_from_ms]
    live_since = [le for le in live_by_eid if parse_event_id(le).close_ms >= compare_from_ms]
    since_matched = sum(1 for x in items_since if x.status == "KHỚP")
    since_compare_from = {
        "compare_from_utc": compare_from_utc,
        "live_total": len(live_since),
        "matched_count": since_matched,
        "missing_count": sum(1 for x in items_since if x.status == "THIẾU"),
        "extra_count": sum(1 for x in items_since if x.status == "THỪA"),
        "new6_virtual_position_count": sum(1 for x in items_since if x.status == "NEW6_VIRTUAL_POSITION"),
        "vps_off_count": sum(1 for x in items_since if x.status == "VPS_TẮT"),
        "time_skew_count": sum(1 for x in items_since if x.status == "LỆCH_THỜI_GIAN"),
        "match_rate_vs_live_pct": round((since_matched / len(live_since) * 100.0), 2) if live_since else None,
        "engine_status_at_compare_from": str(classify_signal_engine_era(compare_from_ms, eras)["engine_status"]),
    }

    matched_cnt = sum(1 for x in items if x.status == "KHỚP")
    return SignalReconcileSummary(
        compare_from_utc=compare_from_utc,
        compare_from_ms=compare_from_ms,
        deploy_23_finalized_utc=deploy_23_finalized_utc,
        since_compare_from=since_compare_from,
        projected_23_finalized_since_compare_from=projected_23,
        era_breakdown=era_breakdown,
        lab_total_in_window=total_lab_in_window,
        live_total=len(live_by_eid),
        matched_count=matched_cnt,
        missing_count=sum(1 for x in items if x.status == "THIẾU"),
        extra_count=sum(1 for x in items if x.status == "THỪA"),
        new6_virtual_position_count=sum(1 for x in items if x.status == "NEW6_VIRTUAL_POSITION"),
        vps_off_count=sum(1 for x in items if x.status == "VPS_TẮT"),
        time_skew_count=sum(1 for x in items if x.status == "LỆCH_THỜI_GIAN"),
        match_rate_vs_live_pct=round((matched_cnt / len(live_by_eid) * 100.0), 2) if live_by_eid else 0.0,
        items=items,
        notes=notes,
    )
