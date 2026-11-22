"""Đối soát tín hiệu Lab vs Live theo đúng engine của từng kỷ nguyên và mốc compare_from."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal, SignalEngine
from autotrade_v3.reconcile_v3.eras import VpsOffWindow, is_in_vps_off_window
from autotrade_v3.reconcile_v3.keys import build_combo_table, parse_event_id
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry

DEFAULT_LIVE_START_MS = 1794614400000  # 2026-11-14T00:00:00Z


def _iso_ms(ts_str: str) -> int:
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# Các kỷ nguyên engine tín hiệu live đã xác nhận từ git log origin/main -- autotrade_v3/live
SIGNAL_ENGINE_ERAS: tuple[dict[str, Any], ...] = (
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
        "engine_ref": "V3_LIVE_20_POSITIVE_FIXED_V1",
        "engine_status": "KHÔNG_DỰNG_ĐƯỢC_ENGINE",
    },
    {
        "era_id": "SIGNAL_ERA_V3_LIVE_23_LAB_5_V1",
        "label_vi": "V3_LIVE_23_LAB_5_V1 (PR #148: 19/11 12:47:21 – 20/11 03:16:27 UTC)",
        "start_utc": "2026-11-19T12:47:21Z",
        "end_utc": "2026-11-20T03:16:27Z",
        "engine_ref": "V3_LIVE_23_LAB_5_V1",
        "engine_status": "KHÔNG_DỰNG_ĐƯỢC_ENGINE",
    },
    {
        "era_id": "SIGNAL_ERA_V3_LIVE_23_LAB_8_V1",
        "label_vi": "V3_LIVE_23_LAB_8_V1 (PR #159: 20/11 03:16:27 – 20/11 04:35:34 UTC)",
        "start_utc": "2026-11-20T03:16:27Z",
        "end_utc": "2026-11-20T04:35:34Z",
        "engine_ref": "V3_LIVE_23_LAB_8_V1",
        "engine_status": "KHÔNG_DỰNG_ĐƯỢC_ENGINE",
    },
    {
        "era_id": "SIGNAL_ERA_V3_LIVE_23_LAB_10_V1",
        "label_vi": "V3_LIVE_23_LAB_10_V1 (PR #160+: từ 20/11 04:35:34 UTC)",
        "start_utc": "2026-11-20T04:35:34Z",
        "end_utc": None,
        "engine_ref": "V3_LIVE_23_LAB_10_V1",
        "engine_status": "KHÔNG_DỰNG_ĐƯỢC_ENGINE",
    },
)


def classify_signal_engine_era(ts_ms: int) -> dict[str, Any]:
    for era in SIGNAL_ENGINE_ERAS:
        s_ms = _iso_ms(era["start_utc"])
        e_ms = _iso_ms(era["end_utc"]) if era["end_utc"] else None
        if ts_ms >= s_ms and (e_ms is None or ts_ms < e_ms):
            return era
    return SIGNAL_ENGINE_ERAS[0]


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
    status: str  # KHỚP | THIẾU | THỪA | VPS_TẮT | NEW6_VIRTUAL_POSITION | LỆCH_THỜI_GIAN | KHÔNG_DỰNG_ĐƯỢC_ENGINE
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
    # Số liệu chính (CHỈ tính từ compare_from)
    since_compare_from: dict[str, Any] = field(default_factory=dict)
    # Tổng hợp theo từng kỷ nguyên engine
    era_breakdown: list[SignalEraBreakdown] = field(default_factory=list)
    # Tổng hợp toàn bộ lịch sử (để hiển thị khối Lịch sử, không dùng kết luận)
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
    live_start_ms: int = DEFAULT_LIVE_START_MS,
) -> SignalReconcileSummary:
    """Đối soát tín hiệu theo đúng engine từng kỷ nguyên; tách riêng NEW6_VIRTUAL_POSITION và compare_from."""
    compare_from_ms = _iso_ms(compare_from_utc)
    strat = StrategyCoreRegistry.get("20FINAL", "1")
    eng = SignalEngine(core_strategy=strat)
    combo_table = build_combo_table()

    # Kỷ nguyên 20FINAL kết thúc tại 2026-11-16T15:06:50Z
    era_20final_end_ms = _iso_ms(str(SIGNAL_ENGINE_ERAS[0]["end_utc"]))
    max_candle_close_ms = max(int(r["close_time"]) for r in candles_15m)
    window_start_ms = live_start_ms
    if live_signals:
        window_start_ms = min(live_start_ms, min(parse_event_id(str(r["event_id"])).close_ms for r in live_signals))

    # Chỉ chạy 20FINAL cho đúng khoảng thời gian kỷ nguyên 20FINAL
    lab_20final_by_eid: dict[str, Signal] = {}
    notes: list[str] = []
    for sym in ("BTC-USDT", "ETH-USDT"):
        df_m15 = _build_m15_df(candles_15m, sym)
        only_warmup = sym == "ETH-USDT"
        if not only_warmup:
            notes.append(
                f"{sym}: live_events/candles.parquet chỉ giữ {len(df_m15)} nến 15m (rolling 3400), "
                "chạy 20FINAL với only_warmup_ready=False cho kỷ nguyên 20FINAL."
            )
        sigs = eng.generate_signals(sym, df_m15, only_approved=True, only_warmup_ready=only_warmup)
        for s in sigs:
            if window_start_ms <= s.signal_close_ms < min(era_20final_end_ms, max_candle_close_ms + 1):
                lab_20final_by_eid[_signal_to_event_id(s)] = s

    live_by_eid: dict[str, dict[str, Any]] = {str(r["event_id"]): r for r in live_signals}
    live_20final_eids = {eid for eid in live_by_eid if parse_event_id(eid).close_ms < era_20final_end_ms}

    items: list[SignalReconcileItem] = []

    # 1. Đối soát kỷ nguyên 20FINAL (engine AVAILABLE)
    matched_20 = set(lab_20final_by_eid.keys()) & live_20final_eids
    lab_only_20 = sorted(set(lab_20final_by_eid.keys()) - live_20final_eids)
    live_only_20 = sorted(live_20final_eids - set(lab_20final_by_eid.keys()))

    skew_lab_used: set[str] = set()
    skew_live_used: set[str] = set()
    for le in live_only_20:
        lp = parse_event_id(le)
        for be in lab_only_20:
            if be in skew_lab_used:
                continue
            bp = parse_event_id(be)
            if (lp.symbol, lp.combo_name, lp.side) == (bp.symbol, bp.combo_name, bp.side):
                diff = lp.close_ms - bp.close_ms
                if 0 < abs(diff) <= 3_600_000:
                    skew_lab_used.add(be)
                    skew_live_used.add(le)
                    items.append(
                        SignalReconcileItem(
                            event_id=le,
                            signal_id=lp.signal_id,
                            symbol=lp.symbol,
                            timeframe=lp.timeframe,
                            combo=lp.combo_name,
                            side=lp.side,
                            signal_close_ms=lp.close_ms,
                            signal_close_utc=_ms_to_iso(lp.close_ms),
                            signal_era_id="SIGNAL_ERA_20FINAL",
                            status="LỆCH_THỜI_GIAN",
                            reason_code="CHƯA_GIẢI_THÍCH",
                            reason_detail_vi=f"Lệch {diff // 1000}s so với tín hiệu Lab {be}",
                            time_diff_ms=diff,
                        )
                    )
                    break

    for eid in sorted(matched_20):
        pk = parse_event_id(eid)
        items.append(
            SignalReconcileItem(
                event_id=eid,
                signal_id=pk.signal_id,
                symbol=pk.symbol,
                timeframe=pk.timeframe,
                combo=pk.combo_name,
                side=pk.side,
                signal_close_ms=pk.close_ms,
                signal_close_utc=_ms_to_iso(pk.close_ms),
                signal_era_id="SIGNAL_ERA_20FINAL",
                status="KHỚP",
                reason_code="KHỚP",
                reason_detail_vi="Khớp chính xác event_id với engine 20FINAL",
            )
        )

    for eid in lab_only_20:
        if eid in skew_lab_used:
            continue
        pk = parse_event_id(eid)
        vps_win = is_in_vps_off_window(pk.close_ms, vps_off_windows)
        if vps_win is not None:
            items.append(
                SignalReconcileItem(
                    event_id=eid,
                    signal_id=pk.signal_id,
                    symbol=pk.symbol,
                    timeframe=pk.timeframe,
                    combo=pk.combo_name,
                    side=pk.side,
                    signal_close_ms=pk.close_ms,
                    signal_close_utc=_ms_to_iso(pk.close_ms),
                    signal_era_id="SIGNAL_ERA_20FINAL",
                    status="VPS_TẮT",
                    reason_code="VPS_TẮT",
                    reason_detail_vi=f"Nằm trong cửa sổ VPS_TẮT ({vps_win.start_utc} -> {vps_win.end_utc})",
                )
            )
            continue

        spec = combo_table.get((pk.symbol, pk.combo_name))
        if spec is not None and spec.is_new6:
            # Tín hiệu thừa của lab do NEW6 virtual_position: tách nhãn riêng, KHÔNG tính vào THIẾU thật
            items.append(
                SignalReconcileItem(
                    event_id=eid,
                    signal_id=pk.signal_id,
                    symbol=pk.symbol,
                    timeframe=pk.timeframe,
                    combo=pk.combo_name,
                    side=pk.side,
                    signal_close_ms=pk.close_ms,
                    signal_close_utc=_ms_to_iso(pk.close_ms),
                    signal_era_id="SIGNAL_ERA_20FINAL",
                    status="NEW6_VIRTUAL_POSITION",
                    reason_code="NEW6_VIRTUAL_POSITION",
                    reason_detail_vi="Lab phát lặp trên các nến 1H theo virtual_position; Live chỉ giữ 1 vị thế thực.",
                )
            )
            continue

        items.append(
            SignalReconcileItem(
                event_id=eid,
                signal_id=pk.signal_id,
                symbol=pk.symbol,
                timeframe=pk.timeframe,
                combo=pk.combo_name,
                side=pk.side,
                signal_close_ms=pk.close_ms,
                signal_close_utc=_ms_to_iso(pk.close_ms),
                signal_era_id="SIGNAL_ERA_20FINAL",
                status="THIẾU",
                reason_code="CHƯA_GIẢI_THÍCH",
                reason_detail_vi="Lab 20FINAL phát tín hiệu nhưng Live không có",
            )
        )

    for eid in live_only_20:
        if eid in skew_live_used:
            continue
        pk = parse_event_id(eid)
        reason_code = "WARMUP_WINDOW_3400" if pk.symbol == "BTC-USDT" else "CHƯA_GIẢI_THÍCH"
        reason_detail = (
            "BTC-USDT chỉ có 3400 nến rolling; warmup/L2 tại thời điểm live có thể khác"
            if pk.symbol == "BTC-USDT"
            else "Live có tín hiệu nhưng 20FINAL không phát"
        )
        items.append(
            SignalReconcileItem(
                event_id=eid,
                signal_id=pk.signal_id,
                symbol=pk.symbol,
                timeframe=pk.timeframe,
                combo=pk.combo_name,
                side=pk.side,
                signal_close_ms=pk.close_ms,
                signal_close_utc=_ms_to_iso(pk.close_ms),
                signal_era_id="SIGNAL_ERA_20FINAL",
                status="THỪA",
                reason_code=reason_code,
                reason_detail_vi=reason_detail,
            )
        )

    # 2. Các kỷ nguyên sau 20FINAL (KHÔNG_DỰNG_ĐƯỢC_ENGINE trong repo lab)
    for eid in sorted(set(live_by_eid.keys()) - live_20final_eids):
        pk = parse_event_id(eid)
        era_info = classify_signal_engine_era(pk.close_ms)
        items.append(
            SignalReconcileItem(
                event_id=eid,
                signal_id=pk.signal_id,
                symbol=pk.symbol,
                timeframe=pk.timeframe,
                combo=pk.combo_name,
                side=pk.side,
                signal_close_ms=pk.close_ms,
                signal_close_utc=_ms_to_iso(pk.close_ms),
                signal_era_id=str(era_info["era_id"]),
                status="KHÔNG_DỰNG_ĐƯỢC_ENGINE",
                reason_code="KHÔNG_DỰNG_ĐƯỢC_ENGINE",
                reason_detail_vi=f"Engine {era_info['engine_ref']} chưa có trong nhánh lab hiện tại",
            )
        )

    items.sort(key=lambda x: (x.signal_close_ms, x.symbol, x.combo, x.status))

    # Tổng hợp theo từng kỷ nguyên engine
    era_breakdown: list[SignalEraBreakdown] = []
    for era in SIGNAL_ENGINE_ERAS:
        eid_key = str(era["era_id"])
        sub = [x for x in items if x.signal_era_id == eid_key]
        s_ms = _iso_ms(era["start_utc"])
        e_ms = _iso_ms(era["end_utc"]) if era["end_utc"] else None
        live_in_era = sum(
            1
            for le in live_by_eid
            if parse_event_id(le).close_ms >= s_ms and (e_ms is None or parse_event_id(le).close_ms < e_ms)
        )
        lab_in_era = len(lab_20final_by_eid) if eid_key == "SIGNAL_ERA_20FINAL" else 0
        era_breakdown.append(
            SignalEraBreakdown(
                era_id=eid_key,
                label_vi=str(era["label_vi"]),
                engine_ref=str(era["engine_ref"]),
                engine_status=str(era["engine_status"]),
                lab_total=lab_in_era,
                live_total=live_in_era,
                matched_count=sum(1 for x in sub if x.status == "KHỚP"),
                missing_count=sum(1 for x in sub if x.status == "THIẾU"),
                extra_count=sum(1 for x in sub if x.status == "THỪA"),
                new6_virtual_position_count=sum(1 for x in sub if x.status == "NEW6_VIRTUAL_POSITION"),
                vps_off_count=sum(1 for x in sub if x.status == "VPS_TẮT"),
                time_skew_count=sum(1 for x in sub if x.status == "LỆCH_THỜI_GIAN"),
            )
        )

    # Thống kê CHỈ từ compare_from
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
        "engine_status_at_compare_from": str(classify_signal_engine_era(compare_from_ms)["engine_status"]),
    }

    matched_cnt = sum(1 for x in items if x.status == "KHỚP")
    return SignalReconcileSummary(
        compare_from_utc=compare_from_utc,
        compare_from_ms=compare_from_ms,
        since_compare_from=since_compare_from,
        era_breakdown=era_breakdown,
        lab_total_in_window=len(lab_20final_by_eid),
        live_total=len(live_by_eid),
        matched_count=matched_cnt,
        missing_count=sum(1 for x in items if x.status == "THIẾU"),
        extra_count=sum(1 for x in items if x.status == "THỪA"),
        new6_virtual_position_count=sum(1 for x in items if x.status == "NEW6_VIRTUAL_POSITION"),
        vps_off_count=sum(1 for x in items if x.status == "VPS_TẮT"),
        time_skew_count=sum(1 for x in items if x.status == "LỆCH_THỜI_GIAN"),
        match_rate_vs_live_pct=round((matched_cnt / len(live_20final_eids) * 100.0), 2) if live_20final_eids else 0.0,
        items=items,
        notes=notes,
    )
