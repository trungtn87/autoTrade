"""Đối soát từng lệnh (9 hạng mục / event_id) và phân loại nguyên nhân sai lệch."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from autotrade_v3.collector_v3.models import to_decimal_str, to_id_str
from autotrade_v3.reconcile_v3.eras import classify_order_era
from autotrade_v3.reconcile_v3.keys import event_id_to_signal_id, parse_event_id
from autotrade_v3.reconcile_v3.mirror_sim import MirrorOrderResult
from autotrade_v3.reconcile_v3.signals_reconcile import SignalReconcileSummary

ZERO = Decimal("0")
HUNDRED = Decimal("100")
BPS_MULT = Decimal("10000")


def _d(val: Any, default: Decimal = ZERO) -> Decimal:
    if val is None or val == "":
        return default
    return Decimal(str(val))


def _d_str(val: Decimal | None) -> str | None:
    if val is None:
        return None
    return to_decimal_str(val, allow_none=True)


def _pct_diff(live_val: Decimal, sim_val: Decimal) -> Decimal | None:
    if sim_val == ZERO:
        return None if live_val == ZERO else None
    return ((live_val - sim_val) / abs(sim_val)) * HUNDRED


def _parse_iso_ms(val: Any) -> int | None:
    if val is None or val == "":
        return None
    dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def load_tolerances(path: Path | None = None) -> dict[str, Any]:
    tol_path = path or (Path(__file__).resolve().parent / "tolerances.json")
    return json.loads(tol_path.read_text(encoding="utf-8"))


@dataclass
class OrderReconcileRow:
    event_id: str
    signal_id: str
    symbol: str
    timeframe: str
    combo: str
    side: str
    era: str
    is_fully_closed: bool
    is_shared_position: bool
    shared_peers: list[str]
    bingx_cross_check: dict[str, Any]
    items: dict[str, dict[str, Any]]
    root_cause: str
    root_cause_detail_vi: str
    overall_provenance: str
    system_issue_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _estimate_funding(
    *,
    symbol: str,
    side: str,
    notional: Decimal,
    entry_ms: int | None,
    exit_ms: int | None,
    funding_rows: list[dict[str, Any]],
) -> tuple[Decimal, int, str]:
    """Ước lượng funding (ESTIMATED) từ bảng funding 8h: notional * funding_rate.

    Quy ước hợp đồng vĩnh cửu: funding_rate > 0 => Long (BUY) trả, Short (SELL) nhận.
    """
    if entry_ms is None or exit_ms is None or exit_ms <= entry_ms:
        return ZERO, 0, "Không có khoảng thời gian nắm giữ hợp lệ"
    total = ZERO
    count = 0
    for r in funding_rows:
        fts = int(r["funding_time_ms"])
        if entry_ms < fts <= exit_ms:
            rate = _d(r["funding_rate"])
            # Short nhận khi rate > 0, Long trả khi rate > 0
            signed = (notional * rate) if side == "SELL" else -(notional * rate)
            total += signed
            count += 1
    formula = f"sum(notional * funding_rate * sign[{side}]) qua {count} kỳ 8h trong ({entry_ms}, {exit_ms}]"
    return total, count, formula


def reconcile_orders(
    *,
    live_signals: list[dict[str, Any]],
    execution_orders: list[dict[str, Any]],
    execution_results: list[dict[str, Any]],
    execution_finances: list[dict[str, Any]],
    bingx_orders: list[dict[str, Any]],
    bingx_fills: list[dict[str, Any]],
    funding_rates: dict[str, list[dict[str, Any]]],
    signal_summary: SignalReconcileSummary,
    mirror_base: dict[str, MirrorOrderResult],
    mirror_ideal: dict[str, MirrorOrderResult],
    tolerances: dict[str, Any] | None = None,
) -> list[OrderReconcileRow]:
    """Xây dựng bảng đối soát 9 hạng mục cho từng event_id."""
    tol = tolerances or load_tolerances()

    sig_by_eid = {str(s["event_id"]): s for s in live_signals}
    fin_by_eid = {str(f["event_id"]): f for f in execution_finances}
    res_by_eid: dict[str, list[dict[str, Any]]] = {}
    for r in execution_results:
        res_by_eid.setdefault(str(r["event_id"]), []).append(r)
    sig_rec_by_eid = {it.event_id: it for it in signal_summary.items}

    # Index bằng chứng phụ bingx_orders & bingx_fills theo exact string order_id
    bx_order_by_id = {to_id_str(o["order_id"], field_name="order_id"): o for o in bingx_orders if o.get("order_id")}
    bx_fills_by_oid: dict[str, list[dict[str, Any]]] = {}
    for f in bingx_fills:
        oid = to_id_str(f.get("order_id"), field_name="order_id", allow_none=True)
        if oid:
            bx_fills_by_oid.setdefault(oid, []).append(f)

    # Phát hiện vị thế gộp / đồng thời cùng (symbol, side)
    intervals_by_eid: dict[str, tuple[str, str, int, int]] = {}
    for o in execution_orders:
        eid = str(o["event_id"])
        pk = parse_event_id(eid)
        ems = _parse_iso_ms(o.get("filled_at")) or pk.close_ms
        closes = res_by_eid.get(eid, [])
        xms = max((_parse_iso_ms(c.get("closed_at")) or ems) for c in closes) if closes else ems
        intervals_by_eid[eid] = (pk.symbol, pk.side, ems, xms)

    peers_by_eid: dict[str, list[str]] = {}
    for eid, (sym, side, s1, e1) in intervals_by_eid.items():
        peers: list[str] = []
        for other_eid, (osym, oside, s2, e2) in intervals_by_eid.items():
            if other_eid == eid:
                continue
            if sym == osym and side == oside and max(s1, s2) <= min(e1, e2):
                peers.append(other_eid)
        peers_by_eid[eid] = sorted(peers)

    slip_warn = _d(tol["entry_slippage_bps_warn"])
    slip_sys = _d(tol["entry_slippage_bps_system"])
    tp_sl_tol_pct = _d(tol["tp_sl_price_diff_pct_tol"])
    fee_tol_usd = _d(tol["fee_diff_usd_tol"])
    pnl_tol_usd = _d(tol["pnl_diff_usd_tol"])
    pnl_tol_pct = _d(tol["pnl_diff_pct_tol"])

    rows: list[OrderReconcileRow] = []
    sorted_orders = sorted(execution_orders, key=lambda o: (parse_event_id(str(o["event_id"])).close_ms, str(o["event_id"])))

    for o in sorted_orders:
        eid = str(o["event_id"])
        pk = parse_event_id(eid)
        sid = event_id_to_signal_id(eid)
        era = classify_order_era(o)
        sig = sig_by_eid.get(eid, {})
        fin = fin_by_eid.get(eid, {})
        legs_live = sorted(res_by_eid.get(eid, []), key=lambda x: str(x.get("closed_at") or ""))
        sim_b = mirror_base.get(eid)
        sim_i = mirror_ideal.get(eid)
        sig_rec = sig_rec_by_eid.get(eid)

        # Bằng chứng phụ BingX (chỉ có đến 14/11 15:36 UTC)
        entry_oid = to_id_str(o.get("entry_order_id"), field_name="entry_order_id", allow_none=True)
        bx_ord = bx_order_by_id.get(entry_oid) if entry_oid else None
        bx_entry_fills = bx_fills_by_oid.get(entry_oid, []) if entry_oid else []
        bingx_cross = {
            "entry_order_id": entry_oid,
            "in_bingx_orders": bx_ord is not None,
            "bingx_order_avg_price": to_decimal_str(bx_ord["avg_price"], allow_none=True) if bx_ord else None,
            "bingx_fills_count": len(bx_entry_fills),
        }

        # 1. Tín hiệu
        item_signal = {
            "live_signal_time": sig.get("signal_time"),
            "live_ref_entry": to_decimal_str(sig.get("entry"), allow_none=True),
            "reconcile_status": sig_rec.status if sig_rec else "UNKNOWN",
            "reason_code": sig_rec.reason_code if sig_rec else "CHƯA_GIẢI_THÍCH",
            "provenance": "EXACT",
        }

        # 2. Giá Entry & trượt giá
        expected_entry = _d(sig.get("entry"))
        live_avg_entry = _d(o.get("avg_price"))
        # Trượt giá bất lợi dương (+): BUY khớp cao hơn kỳ vọng, SELL khớp thấp hơn kỳ vọng
        if expected_entry > ZERO and live_avg_entry > ZERO:
            if pk.side == "BUY":
                live_slip_bps = ((live_avg_entry - expected_entry) / expected_entry) * BPS_MULT
            else:
                live_slip_bps = ((expected_entry - live_avg_entry) / expected_entry) * BPS_MULT
        else:
            live_slip_bps = ZERO

        sim_entry_b = _d(sim_b.entry_price) if (sim_b and sim_b.entry_price is not None) else None
        sim_entry_raw = _d(sim_b.entry_px_raw) if (sim_b and sim_b.entry_px_raw is not None) else None
        entry_abs_diff = (live_avg_entry - sim_entry_b) if sim_entry_b is not None else None
        entry_pct_diff = _pct_diff(live_avg_entry, sim_entry_b) if sim_entry_b is not None else None

        item_entry = {
            "expected_entry": _d_str(expected_entry),
            "live_avg_price": _d_str(live_avg_entry),
            "live_slippage_bps": _d_str(live_slip_bps.quantize(Decimal("0.01"))),
            "sim_base_entry": _d_str(sim_entry_b),
            "sim_raw_entry_m1_open": _d_str(sim_entry_raw),
            "sim_ideal_entry": _d_str(_d(sim_i.entry_price) if (sim_i and sim_i.entry_price is not None) else None),
            "abs_diff": _d_str(entry_abs_diff),
            "pct_diff": _d_str(entry_pct_diff.quantize(Decimal("0.0001"))) if entry_pct_diff is not None else None,
            "provenance": "EXACT",
        }

        # 3. TP / SL
        live_tp = _d(o.get("tp_price") or sig.get("tp"))
        live_sl = _d(o.get("sl_price") or sig.get("sl"))
        sig_tp = _d(sig.get("tp"))
        sig_sl = _d(sig.get("sl"))
        tp_diff_pct = _pct_diff(live_tp, sig_tp) if sig_tp > ZERO else ZERO
        sl_diff_pct = _pct_diff(live_sl, sig_sl) if sig_sl > ZERO else ZERO
        item_tp_sl = {
            "live_tp_price": _d_str(live_tp),
            "live_sl_price": _d_str(live_sl),
            "live_tp_status": str(o.get("tp_status") or ""),
            "live_sl_status": str(o.get("sl_status") or ""),
            "tp_order_id": to_id_str(o.get("tp_order_id"), field_name="tp_order_id", allow_none=True),
            "sl_order_id": to_id_str(o.get("sl_order_id"), field_name="sl_order_id", allow_none=True),
            "sim_ref_tp_price": _d_str(sig_tp),
            "sim_ref_sl_price": _d_str(sig_sl),
            "tp_pct_diff": _d_str(tp_diff_pct.quantize(Decimal("0.0001"))) if tp_diff_pct is not None else "0",
            "sl_pct_diff": _d_str(sl_diff_pct.quantize(Decimal("0.0001"))) if sl_diff_pct is not None else "0",
            "provenance": "EXACT",
        }

        # 4. Trailing
        if era == "ERA_1_50TP_50TRAIL":
            item_trailing = {
                "applicable": True,
                "trailing_order_id": to_id_str(o.get("trailing_order_id"), field_name="trailing_order_id", allow_none=True),
                "activation_price": to_decimal_str(o.get("trailing_activation_price"), allow_none=True),
                "callback_rate": to_decimal_str(o.get("trailing_callback_rate"), allow_none=True),
                "trailing_status": str(o.get("trailing_status") or ""),
                "sim_trailing_act_pct": "0.005",
                "sim_trailing_cb_pct": "0.005",
                "provenance": "EXACT",
            }
        else:
            item_trailing = {
                "applicable": False,
                "note": "Kỷ nguyên FIXED_TP_SL không dùng trailing",
                "provenance": "EXACT",
            }

        # 5. Từng leg và vị thế gộp
        peers = peers_by_eid[eid]
        is_shared = len(peers) > 0
        live_legs_formatted = [
            {
                "close_order_id": to_id_str(r.get("close_order_id"), field_name="close_order_id", allow_none=True),
                "close_type": str(r.get("close_type") or ""),
                "closed_qty": to_decimal_str(r.get("closed_qty")),
                "close_price": to_decimal_str(r.get("close_price")),
                "realized_pnl": to_decimal_str(r.get("realized_pnl")),
                "fee": to_decimal_str(r.get("fee")),
                "closed_at": r.get("closed_at"),
            }
            for r in legs_live
        ]
        sim_legs_formatted = (
            [
                {
                    "leg_id": lg.leg_id,
                    "leg_type": lg.leg_type,
                    "qty": to_decimal_str(lg.qty),
                    "exit_price": to_decimal_str(lg.exit_price),
                    "exit_reason": lg.exit_reason,
                    "gross_pnl": to_decimal_str(lg.gross_pnl),
                    "fee_total": to_decimal_str(lg.fee_total),
                    "net_pnl": to_decimal_str(lg.net_pnl),
                }
                for lg in sim_b.legs
            ]
            if sim_b
            else []
        )
        item_legs = {
            "live_executed_qty": to_decimal_str(o.get("executed_qty")),
            "live_closed_qty": to_decimal_str(fin.get("closed_qty"), allow_none=True),
            "live_legs_count": len(live_legs_formatted),
            "live_legs": live_legs_formatted,
            "sim_legs_count": len(sim_legs_formatted),
            "sim_legs": sim_legs_formatted,
            "is_shared_position": is_shared,
            "shared_peers": peers,
            "provenance": "RECONCILED" if is_shared else "EXACT",
        }

        # 6. Phí vào / ra
        live_open_fee = _d(fin.get("open_fee"))
        live_close_fee = _d(fin.get("close_fee"))
        live_total_fee = live_open_fee + live_close_fee
        sim_fee_b = _d(sim_b.fee_total) if sim_b else ZERO
        sim_fee_i = _d(sim_i.fee_total) if sim_i else ZERO
        fee_abs_diff = live_total_fee - sim_fee_b
        fee_pct_diff = _pct_diff(live_total_fee, sim_fee_b)
        item_fees = {
            "live_open_fee": _d_str(live_open_fee),
            "live_close_fee": _d_str(live_close_fee),
            "live_total_fee": _d_str(live_total_fee),
            "sim_base_fee": _d_str(sim_fee_b),
            "sim_ideal_fee": _d_str(sim_fee_i),
            "abs_diff": _d_str(fee_abs_diff),
            "pct_diff": _d_str(fee_pct_diff.quantize(Decimal("0.01"))) if fee_pct_diff is not None else None,
            "provenance": "EXACT" if fin else "UNRESOLVED",
        }

        # 7. Funding (CHỈ ESTIMATED, không cộng vào PnL thực)
        _, _, entry_ms, exit_ms = intervals_by_eid[eid]
        notional_live = live_avg_entry * _d(o.get("executed_qty"))
        est_funding, fund_periods, fund_formula = _estimate_funding(
            symbol=pk.symbol,
            side=pk.side,
            notional=notional_live if notional_live > ZERO else Decimal("100"),
            entry_ms=entry_ms,
            exit_ms=exit_ms,
            funding_rows=funding_rates.get(pk.symbol, []),
        )
        item_funding = {
            "live_db_funding": None,  # NULL theo thiết kế
            "estimated_funding_usd": _d_str(est_funding.quantize(Decimal("0.000001"))),
            "funding_periods": fund_periods,
            "formula": fund_formula,
            "included_in_live_net_pnl": False,
            "provenance": "ESTIMATED",
        }

        # 8. PnL gross / net
        live_gross = _d(fin.get("gross_pnl"))
        live_net = _d(fin.get("net_pnl"))
        sum_leg_pnl = sum((_d(r.get("realized_pnl")) for r in legs_live), ZERO)
        is_fully_closed = bool(fin.get("is_fully_closed"))
        sim_gross_b = _d(sim_b.gross_pnl) if sim_b else ZERO
        sim_net_b = _d(sim_b.net_pnl) if sim_b else ZERO
        sim_gross_i = _d(sim_i.gross_pnl) if sim_i else ZERO
        sim_net_i = _d(sim_i.net_pnl) if sim_i else ZERO

        pnl_abs_diff = live_net - sim_net_b
        pnl_pct_diff = _pct_diff(live_net, sim_net_b)
        gross_abs_diff = live_gross - sim_gross_b
        gross_pct_diff = _pct_diff(live_gross, sim_gross_b)

        pnl_prov = "EXACT"
        if not fin or not is_fully_closed:
            pnl_prov = "UNRESOLVED"
        elif abs(sum_leg_pnl - live_gross) > Decimal("0.0001"):
            pnl_prov = "RECONCILED"

        item_pnl = {
            "live_gross_pnl": _d_str(live_gross),
            "live_net_pnl": _d_str(live_net),
            "live_results_sum_pnl": _d_str(sum_leg_pnl),
            "sim_base_gross_pnl": _d_str(sim_gross_b),
            "sim_base_net_pnl": _d_str(sim_net_b),
            "sim_ideal_gross_pnl": _d_str(sim_gross_i),
            "sim_ideal_net_pnl": _d_str(sim_net_i),
            "gross_abs_diff": _d_str(gross_abs_diff),
            "gross_pct_diff": _d_str(gross_pct_diff.quantize(Decimal("0.01"))) if gross_pct_diff is not None else None,
            "net_abs_diff": _d_str(pnl_abs_diff),
            "net_pct_diff": _d_str(pnl_pct_diff.quantize(Decimal("0.01"))) if pnl_pct_diff is not None else None,
            "provenance": pnl_prov,
        }

        # 9. Kết quả đóng
        live_types = [str(r.get("close_type") or "OTHER") for r in legs_live]
        live_outcome = "+".join(live_types) if live_types else "OPEN"
        sim_types = [lg.exit_reason for lg in sim_b.legs] if sim_b else ["MISSING"]
        sim_outcome_b = sim_b.exit_outcome if sim_b else "MISSING"
        sim_outcome_i = sim_i.exit_outcome if sim_i else "MISSING"
        matched_outcome = bool(live_types and sim_b and sorted(live_types) == sorted(sim_types))
        item_outcome = {
            "live_outcome": live_outcome,
            "sim_base_outcome": sim_outcome_b,
            "sim_ideal_outcome": sim_outcome_i,
            "matched_outcome": matched_outcome,
            "provenance": "EXACT" if legs_live else "UNRESOLVED",
        }

        # Phân loại nguyên nhân sai lệch vượt dung sai
        root_cause = "TRONG_DUNG_SAI"
        root_detail = "Các hạng mục chính nằm trong ngưỡng dung sai cấu hình"
        sys_note: str | None = None

        exec_qty = _d(o.get("executed_qty"))
        closed_qty = _d(fin.get("closed_qty"))
        if str(o.get("entry_status") or "") != "FILLED" or not is_fully_closed or abs(exec_qty - closed_qty) > Decimal("1e-8"):
            root_cause = "HỆ_THỐNG"
            root_detail = f"Trạng thái lệnh/đóng chưa hoàn chỉnh: entry_status={o.get('entry_status')}, exec_qty={exec_qty}, closed_qty={closed_qty}"
            sys_note = root_detail
        elif abs(live_slip_bps) >= slip_sys:
            root_cause = "HỆ_THỐNG"
            root_detail = f"Trượt giá vào lệnh bất thường ({live_slip_bps:.2f} bps >= ngưỡng hệ thống {slip_sys} bps)"
            sys_note = root_detail
        elif abs(sum_leg_pnl - live_gross) > Decimal("0.01"):
            root_cause = "HỆ_THỐNG"
            root_detail = f"Tổng realized_pnl các leg ({sum_leg_pnl}) lệch gross_pnl ({live_gross})"
            sys_note = root_detail
        elif sim_b is None or sim_b.status != "ACCEPTED":
            root_cause = "DỮ_LIỆU"
            root_detail = f"Mô phỏng M1 không khớp lệnh (status={sim_b.status if sim_b else 'MISSING'})"
        elif sig_rec is not None and sig_rec.status != "KHỚP":
            if sig_rec.reason_code == "L2_REGISTRY_CHANGED":
                root_cause = "CHIẾN_LƯỢC"
                root_detail = sig_rec.reason_detail_vi
            elif sig_rec.reason_code == "WARMUP_WINDOW_3400":
                root_cause = "DỮ_LIỆU"
                root_detail = sig_rec.reason_detail_vi
            else:
                root_cause = "CHƯA_GIẢI_THÍCH"
                root_detail = sig_rec.reason_detail_vi
        elif (
            abs(pnl_abs_diff) > pnl_tol_usd
            and (pnl_pct_diff is None or abs(pnl_pct_diff) > pnl_tol_pct)
        ) or not matched_outcome or abs(live_slip_bps) > slip_warn or abs(fee_abs_diff) > fee_tol_usd:
            root_cause = "MÔ_HÌNH_THỰC_THI"
            reasons: list[str] = []
            if not matched_outcome:
                reasons.append(f"Kết quả đóng khác nhau (Live={live_outcome} vs Sim={sim_outcome_b})")
            if abs(pnl_abs_diff) > pnl_tol_usd:
                reasons.append(f"Lệch PnL ròng {pnl_abs_diff:+.4f} USD")
            if abs(live_slip_bps) > slip_warn:
                reasons.append(f"Trượt giá vào {live_slip_bps:+.2f} bps")
            if abs(fee_abs_diff) > fee_tol_usd:
                reasons.append(f"Lệch phí {fee_abs_diff:+.4f} USD")
            root_detail = "; ".join(reasons)
        elif (tp_diff_pct is not None and abs(tp_diff_pct) > tp_sl_tol_pct) or (sl_diff_pct is not None and abs(sl_diff_pct) > tp_sl_tol_pct):
            root_cause = "HỆ_THỐNG"
            root_detail = f"Giá đặt TP/SL lệch so với tín hiệu (tp_diff={tp_diff_pct}%, sl_diff={sl_diff_pct}%)"
            sys_note = root_detail

        rows.append(
            OrderReconcileRow(
                event_id=eid,
                signal_id=sid,
                symbol=pk.symbol,
                timeframe=pk.timeframe,
                combo=pk.combo_name,
                side=pk.side,
                era=era,
                is_fully_closed=is_fully_closed,
                is_shared_position=is_shared,
                shared_peers=peers,
                bingx_cross_check=bingx_cross,
                items={
                    "signal": item_signal,
                    "entry": item_entry,
                    "tp_sl": item_tp_sl,
                    "trailing": item_trailing,
                    "legs_and_merged": item_legs,
                    "fees": item_fees,
                    "funding": item_funding,
                    "pnl": item_pnl,
                    "close_outcome": item_outcome,
                },
                root_cause=root_cause,
                root_cause_detail_vi=root_detail,
                overall_provenance=pnl_prov,
                system_issue_note=sys_note,
            )
        )

    return rows
