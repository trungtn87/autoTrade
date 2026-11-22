"""Mô phỏng LIVE_MIRROR cho chính các tín hiệu live đã vào lệnh trên M1 với BASE và IDEAL."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.reconcile_v3.eras import ERA_1_50TP_50TRAIL, classify_order_era
from autotrade_v3.reconcile_v3.keys import build_combo_table, parse_event_id
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.sim.engine import UnifiedSimulator
from autotrade_v3.strategy_core.api import ExitPolicy


@dataclass(frozen=True)
class MirrorLegResult:
    leg_id: str
    leg_type: str
    entry_price: float
    entry_px_raw: float
    exit_price: float
    exit_reason: str
    qty: float
    notional: float
    gross_pnl: float
    fee_entry: float
    fee_exit: float
    fee_total: float
    net_pnl: float
    entry_time_ms: int
    exit_time_ms: int


@dataclass
class MirrorOrderResult:
    event_id: str
    signal_id: str
    profile: str
    status: str  # ACCEPTED | UNFILLABLE | MISSING
    entry_price: float | None
    entry_px_raw: float | None
    gross_pnl: float
    fee_entry: float
    fee_exit: float
    fee_total: float
    net_pnl: float
    exit_outcome: str
    entry_time_ms: int | None
    exit_time_ms: int | None
    legs: list[MirrorLegResult] = field(default_factory=list)


def _build_m1_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    sorted_rows = sorted(rows, key=lambda x: int(x["open_time_ms"]))
    return pd.DataFrame(
        {
            "open": [float(r["open"]) for r in sorted_rows],
            "high": [float(r["high"]) for r in sorted_rows],
            "low": [float(r["low"]) for r in sorted_rows],
            "close": [float(r["close"]) for r in sorted_rows],
            "volume": [float(r["volume"]) for r in sorted_rows],
            "provider": ["BINGX"] * len(sorted_rows),
        },
        index=pd.to_datetime([int(r["open_time_ms"]) for r in sorted_rows], unit="ms", utc=True),
    )


def _build_mirror_signals(
    live_signals: list[dict[str, Any]],
    execution_orders: list[dict[str, Any]],
) -> list[tuple[str, Signal]]:
    combo_table = build_combo_table()
    orders_by_eid = {str(o["event_id"]): o for o in execution_orders}
    out: list[tuple[str, Signal]] = []

    for sig_row in live_signals:
        eid = str(sig_row["event_id"])
        order_row = orders_by_eid.get(eid)
        if order_row is None:
            continue
        pk = parse_event_id(eid)
        spec = combo_table[(pk.symbol, pk.combo_name)]
        era = classify_order_era(order_row)

        if era == ERA_1_50TP_50TRAIL:
            act_pct = float(order_row.get("trailing_activation_price") or 0)
            # trailing_callback_rate trong DB là tỷ lệ (vd 0.005), mặc định 0.005
            cb_raw = order_row.get("trailing_callback_rate")
            cb_pct = float(cb_raw) if cb_raw not in (None, "") else 0.005
            # Nếu live activation price được ghi, tính % tương đối so với entry, mặc định 0.005
            entry_ref = float(sig_row["entry"])
            act_rel = abs(act_pct - entry_ref) / entry_ref if act_pct > 0 and entry_ref > 0 else 0.005
            policy = ExitPolicy(
                tp_pct=float(spec.exit_policy.tp_pct),
                sl_pct=float(spec.exit_policy.sl_pct),
                split_legs=True,
                trailing_act_pct=act_rel if act_rel > 0 else 0.005,
                trailing_cb_pct=cb_pct if cb_pct > 0 else 0.005,
                leg_weights=(0.5, 0.5),
            )
        else:
            policy = ExitPolicy(
                tp_pct=float(spec.exit_policy.tp_pct),
                sl_pct=float(spec.exit_policy.sl_pct),
                split_legs=False,
                leg_weights=(1.0,),
            )

        sig = Signal(
            symbol=pk.symbol,
            combo_id=int(spec.combo_id),
            combo_name=spec.combo_name,
            timeframe=pk.timeframe,
            side=pk.side,  # type: ignore[arg-type]
            signal_close_ms=pk.close_ms,
            signal_close_dt=pd.to_datetime(pk.close_ms, unit="ms", utc=True),
            bar_position=0,
            bar_open_ms=pk.close_ms - (3_599_999 if pk.timeframe == "1h" else 899_999),
            entry_price=float(sig_row["entry"]),
            tp_pct=float(spec.exit_policy.tp_pct),
            sl_pct=float(spec.exit_policy.sl_pct),
            tp_price=float(sig_row["tp"]),
            sl_price=float(sig_row["sl"]),
            approved=True,
            warmup_ready=True,
            smc_dir=0,
            ob_blocked=False,
            provider="BINGX",
            is_new6=bool(spec.is_new6),
            exit_policy=policy,
            exit_policy_hash=policy.get_policy_hash(),
            signal_id=pk.signal_id,
        )
        out.append((eid, sig))
    return out


def _summarize_exit_outcome(legs: list[MirrorLegResult]) -> str:
    if not legs:
        return "OPEN_OR_UNFILLED"
    reasons = [lg.exit_reason for lg in legs]
    if len(reasons) == 1:
        return reasons[0]
    return "+".join(reasons)


def run_live_mirror_simulation(
    *,
    live_signals: list[dict[str, Any]],
    execution_orders: list[dict[str, Any]],
    candles_1m: dict[str, list[dict[str, Any]]],
    notional_per_trade: float = 100.0,
    leverage: float = 50.0,
) -> dict[str, dict[str, MirrorOrderResult]]:
    """Chạy mô phỏng LIVE_MIRROR cho cả 2 hồ sơ chi phí: BASE (mặc định) và IDEAL (tham chiếu).

    Trả về dict[profile_name, dict[event_id, MirrorOrderResult]].
    """
    candle_dict = {sym: _build_m1_df(rows) for sym, rows in candles_1m.items() if rows}
    pairs = _build_mirror_signals(live_signals, execution_orders)
    signals = [sig for _, sig in pairs]
    sid_to_eid = {sig.signal_id: eid for eid, sig in pairs}

    results_by_profile: dict[str, dict[str, MirrorOrderResult]] = {}
    for profile_name in ("BASE", "IDEAL"):
        cost_model = CostModel.from_profile_name(profile_name, is_real_data=True)
        risk = RiskConfig(
            initial_equity=1000.0,
            notional_per_trade=notional_per_trade,
            leverage_btc=leverage,
            leverage_eth=leverage,
            cost_profile=profile_name,
            cost_model=cost_model,
        )
        sim = UnifiedSimulator(risk_config=risk, cost_model=cost_model)
        sim_res = sim.run(mode="ALL_SIGNALS", signals=signals, candle_dict=candle_dict)

        dec_by_sid = {d.signal_id: d for d in sim_res.signal_decisions}
        trades_by_sid: dict[str, list[Any]] = {}
        for tr in sim_res.trades:
            trades_by_sid.setdefault(str(tr.signal_id), []).append(tr)

        profile_map: dict[str, MirrorOrderResult] = {}
        for eid, sig in pairs:
            sid = sig.signal_id
            dec = dec_by_sid.get(sid)
            tr_list = trades_by_sid.get(sid, [])
            if not tr_list:
                profile_map[eid] = MirrorOrderResult(
                    event_id=eid,
                    signal_id=sid,
                    profile=profile_name,
                    status=dec.status if dec else "MISSING",
                    entry_price=None,
                    entry_px_raw=None,
                    gross_pnl=0.0,
                    fee_entry=0.0,
                    fee_exit=0.0,
                    fee_total=0.0,
                    net_pnl=0.0,
                    exit_outcome="UNFILLABLE",
                    entry_time_ms=None,
                    exit_time_ms=None,
                    legs=[],
                )
                continue

            legs: list[MirrorLegResult] = []
            for tr in sorted(tr_list, key=lambda x: str(x.leg_id)):
                legs.append(
                    MirrorLegResult(
                        leg_id=str(tr.leg_id),
                        leg_type=str(tr.leg_type),
                        entry_price=float(tr.entry_price),
                        entry_px_raw=float(tr.entry_px_raw),
                        exit_price=float(tr.exit_price),
                        exit_reason=str(tr.exit_reason),
                        qty=float(tr.qty),
                        notional=float(tr.notional),
                        gross_pnl=float(tr.gross_pnl),
                        fee_entry=float(tr.fee_entry),
                        fee_exit=float(tr.fee_exit),
                        fee_total=float(tr.fee_total),
                        net_pnl=float(tr.net_pnl),
                        entry_time_ms=int(tr.entry_time_ms),
                        exit_time_ms=int(tr.exit_time_ms),
                    )
                )
            profile_map[eid] = MirrorOrderResult(
                event_id=eid,
                signal_id=sid,
                profile=profile_name,
                status="ACCEPTED",
                entry_price=legs[0].entry_price,
                entry_px_raw=legs[0].entry_px_raw,
                gross_pnl=sum(l.gross_pnl for l in legs),
                fee_entry=sum(l.fee_entry for l in legs),
                fee_exit=sum(l.fee_exit for l in legs),
                fee_total=sum(l.fee_total for l in legs),
                net_pnl=sum(l.net_pnl for l in legs),
                exit_outcome=_summarize_exit_outcome(legs),
                entry_time_ms=min(l.entry_time_ms for l in legs),
                exit_time_ms=max(l.exit_time_ms for l in legs),
                legs=legs,
            )
        results_by_profile[profile_name] = profile_map
        _ = sid_to_eid  # keep reference clear

    return results_by_profile
