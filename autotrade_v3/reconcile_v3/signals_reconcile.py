"""Đối soát tín hiệu Lab (20FINAL) vs Live (strategy_signals) trên khoảng thời gian live."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal, SignalEngine
from autotrade_v3.reconcile_v3.eras import VpsOffWindow, is_in_vps_off_window
from autotrade_v3.reconcile_v3.keys import build_combo_table, parse_event_id
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry

# Thời điểm live bắt đầu (2026-11-14T00:00:00Z)
DEFAULT_LIVE_START_MS = 1794614400000
# PR #129 (41ab58e, 2026-11-16 22:06:50 +0700 = 15:06:50 UTC) bắt đầu thay đổi L2 registry live
L2_REGISTRY_CHANGE_MS = int(datetime(2026, 11, 16, 15, 6, 50, tzinfo=timezone.utc).timestamp() * 1000)


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
    status: str  # KHỚP | THIẾU | THỪA | VPS_TẮT | LỆCH_THỜI_GIAN
    reason_code: str
    reason_detail_vi: str
    time_diff_ms: int = 0


@dataclass
class SignalReconcileSummary:
    lab_total_in_window: int
    live_total: int
    matched_count: int
    missing_count: int
    extra_count: int
    vps_off_count: int
    time_skew_count: int
    match_rate_vs_live_pct: float
    items: list[SignalReconcileItem]
    notes: list[str]


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
    live_start_ms: int = DEFAULT_LIVE_START_MS,
) -> SignalReconcileSummary:
    """Chạy lại engine tín hiệu 20FINAL trên nến 15m qua khoảng live và đối soát với strategy_signals."""
    strat = StrategyCoreRegistry.get("20FINAL", "1")
    eng = SignalEngine(core_strategy=strat)
    combo_table = build_combo_table()

    # Khoảng thời gian phủ của nến 15m
    max_candle_close_ms = max(int(r["close_time"]) for r in candles_15m)
    if live_signals:
        min_live_ms = min(parse_event_id(str(r["event_id"])).close_ms for r in live_signals)
        window_start_ms = min(live_start_ms, min_live_ms)
    else:
        window_start_ms = live_start_ms

    lab_by_eid: dict[str, Signal] = {}
    notes: list[str] = []
    for sym in ("BTC-USDT", "ETH-USDT"):
        df_m15 = _build_m15_df(candles_15m, sym)
        # BTC-USDT trong live_events chỉ lưu 3400 nến rolling nên only_warmup_ready=False để kiểm tra các nến đầu cửa sổ
        only_warmup = sym == "ETH-USDT"
        if not only_warmup:
            notes.append(
                f"{sym}: live_events/candles.parquet chỉ giữ {len(df_m15)} nến 15m (rolling 3400), "
                "chạy replay với only_warmup_ready=False để bao phủ từ 14/11."
            )
        sigs = eng.generate_signals(sym, df_m15, only_approved=True, only_warmup_ready=only_warmup)
        for s in sigs:
            if window_start_ms <= s.signal_close_ms <= max_candle_close_ms:
                eid = _signal_to_event_id(s)
                lab_by_eid[eid] = s

    live_by_eid: dict[str, dict[str, Any]] = {str(r["event_id"]): r for r in live_signals}

    # Xây dựng khoảng nắm giữ thực tế theo (symbol, combo) từ execution_orders & execution_results
    live_active_intervals: dict[tuple[str, str], list[tuple[int, int]]] = {}
    res_by_eid: dict[str, list[dict[str, Any]]] = {}
    for r in execution_results:
        res_by_eid.setdefault(str(r["event_id"]), []).append(r)
    for o in execution_orders:
        eid = str(o["event_id"])
        pk = parse_event_id(eid)
        filled_ms = pk.close_ms
        if o.get("filled_at"):
            dt = datetime.fromisoformat(str(o["filled_at"]).replace("Z", "+00:00"))
            filled_ms = int(dt.timestamp() * 1000)
        closes = res_by_eid.get(eid, [])
        if closes:
            last_close_ms = max(
                int(datetime.fromisoformat(str(c["closed_at"]).replace("Z", "+00:00")).timestamp() * 1000)
                for c in closes
                if c.get("closed_at")
            )
        else:
            last_close_ms = filled_ms
        live_active_intervals.setdefault((pk.symbol, pk.combo_name), []).append((filled_ms, last_close_ms))

    items: list[SignalReconcileItem] = []
    matched_eids = set(lab_by_eid.keys()) & set(live_by_eid.keys())
    lab_only_eids = sorted(set(lab_by_eid.keys()) - set(live_by_eid.keys()))
    live_only_eids = sorted(set(live_by_eid.keys()) - set(lab_by_eid.keys()))

    # Kiểm tra lệch thời gian (±1h cùng symbol, combo, side)
    skew_lab_used: set[str] = set()
    skew_live_used: set[str] = set()
    for live_eid in live_only_eids:
        lp = parse_event_id(live_eid)
        for lab_eid in lab_only_eids:
            if lab_eid in skew_lab_used:
                continue
            bp = parse_event_id(lab_eid)
            if (lp.symbol, lp.combo_name, lp.side) == (bp.symbol, bp.combo_name, bp.side):
                diff = lp.close_ms - bp.close_ms
                if 0 < abs(diff) <= 3_600_000:
                    skew_lab_used.add(lab_eid)
                    skew_live_used.add(live_eid)
                    items.append(
                        SignalReconcileItem(
                            event_id=live_eid,
                            signal_id=f"SIG_{lp.symbol.replace('-', '')}_{lp.combo_name}_{lp.timeframe}_{lp.side}_{lp.close_ms}",
                            symbol=lp.symbol,
                            timeframe=lp.timeframe,
                            combo=lp.combo_name,
                            side=lp.side,
                            signal_close_ms=lp.close_ms,
                            signal_close_utc=_ms_to_iso(lp.close_ms),
                            status="LỆCH_THỜI_GIAN",
                            reason_code="CHƯA_GIẢI_THÍCH",
                            reason_detail_vi=f"Lệch {diff // 1000}s so với tín hiệu Lab {lab_eid}",
                            time_diff_ms=diff,
                        )
                    )
                    break

    for eid in sorted(matched_eids):
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
                status="KHỚP",
                reason_code="KHỚP",
                reason_detail_vi="Khớp chính xác event_id giữa Lab 20FINAL và Live",
            )
        )

    for eid in lab_only_eids:
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
                    status="VPS_TẮT",
                    reason_code="VPS_TẮT",
                    reason_detail_vi=f"Nằm trong cửa sổ VPS_TẮT ({vps_win.start_utc} -> {vps_win.end_utc})",
                )
            )
            continue

        # Tìm lý do có bằng chứng cho tín hiệu Lab có mà Live không có
        spec = combo_table.get((pk.symbol, pk.combo_name))
        reason_code = "CHƯA_GIẢI_THÍCH"
        reason_detail = "Không tìm thấy bằng chứng trực tiếp trong log/kỷ nguyên"

        if pk.close_ms >= L2_REGISTRY_CHANGE_MS:
            reason_code = "L2_REGISTRY_CHANGED"
            reason_detail = "Sau PR #129/#148/#159/#160 (16–20/11), bộ lọc/registry L2 live đã đổi khỏi 20FINAL"
        elif spec is not None and spec.is_new6:
            # Kiểm tra xem live có vị thế hoặc tín hiệu NEW6 gần đó không
            intervals = live_active_intervals.get((pk.symbol, pk.combo_name), [])
            in_active = any(start <= pk.close_ms <= end + 3_600_000 for start, end in intervals)
            if in_active or intervals:
                reason_code = "NEW6_VIRTUAL_POSITION"
                reason_detail = "NEW6 virtual_position: Lab phát tín hiệu lặp theo trạng thái ảo khác với trạng thái lệnh đang mở trên Live"

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
                status="THIẾU",
                reason_code=reason_code,
                reason_detail_vi=reason_detail,
            )
        )

    for eid in live_only_eids:
        if eid in skew_live_used:
            continue
        pk = parse_event_id(eid)
        reason_code = "CHƯA_GIẢI_THÍCH"
        reason_detail = "Live có tín hiệu nhưng replay 20FINAL không phát"
        if pk.symbol == "BTC-USDT":
            reason_code = "WARMUP_WINDOW_3400"
            reason_detail = "BTC-USDT chỉ có 3400 nến rolling trong cache; trạng thái warmup/L2 tại thời điểm live có thể khác"
        elif pk.close_ms >= L2_REGISTRY_CHANGE_MS:
            reason_code = "L2_REGISTRY_CHANGED"
            reason_detail = "Sau PR #129/#148/#159/#160, bộ lọc L2 live đã đổi khỏi 20FINAL"

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
                status="THỪA",
                reason_code=reason_code,
                reason_detail_vi=reason_detail,
            )
        )

    items.sort(key=lambda x: (x.signal_close_ms, x.symbol, x.combo, x.status))
    matched_cnt = sum(1 for x in items if x.status == "KHỚP")
    missing_cnt = sum(1 for x in items if x.status == "THIẾU")
    extra_cnt = sum(1 for x in items if x.status == "THỪA")
    vps_off_cnt = sum(1 for x in items if x.status == "VPS_TẮT")
    skew_cnt = sum(1 for x in items if x.status == "LỆCH_THỜI_GIAN")
    match_rate = (matched_cnt / len(live_by_eid) * 100.0) if live_by_eid else 0.0

    return SignalReconcileSummary(
        lab_total_in_window=len(lab_by_eid),
        live_total=len(live_by_eid),
        matched_count=matched_cnt,
        missing_count=missing_cnt,
        extra_count=extra_cnt,
        vps_off_count=vps_off_cnt,
        time_skew_count=skew_cnt,
        match_rate_vs_live_pct=round(match_rate, 2),
        items=items,
        notes=notes,
    )
