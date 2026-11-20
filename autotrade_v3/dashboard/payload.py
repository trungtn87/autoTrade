"""Pure payload builder for the V3 Live Terminal. No I/O: takes row dicts, returns JSON-ready dicts.

Money fields come from ``autotrade_v3.execution_finances`` when present; otherwise the closed net PnL is
``sum(realized_pnl) - sum(fee)`` from ``autotrade_v3.execution_results``. A trade is closed when its
finance row says ``is_fully_closed``. Open positions are filled entries without a fully-closed finance row.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

PUBLIC_TRADE_FIELDS = ("symbol", "combo", "side", "entry_ms", "exit_ms", "close_type", "net_pnl", "roi_pct")
PUBLIC_OPEN_FIELDS = ("symbol", "combo", "side", "entry_ms", "avg_price", "qty", "sl", "tp")
PUBLIC_SIGNAL_FIELDS = ("symbol", "combo", "side", "signal_ms", "entry", "tp", "sl")
PUBLIC_ERROR_FIELDS = ("layer", "symbol", "error_code", "time_ms", "discord_status")


def _f(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _opt(value: Any) -> float | None:
    return float(value) if value is not None else None


def _window_ok(time_ms: int | None, now_ms: int, days: int | None) -> bool:
    if days is None:
        return True
    if time_ms is None:
        return False
    return time_ms >= now_ms - days * 86_400_000


def build_payload(*, orders: list[dict], results: list[dict], finances: dict[int, dict],
                  signals: list[dict], errors: list[dict], candle_last_close: dict[str, int],
                  now_ms: int, symbol: str = "", combo: str = "", days: int | None = 30,
                  public: bool = False) -> dict:
    """Build the dashboard JSON.

    ``orders``: execution_orders rows with ``id, event_id, symbol, combo, side, executed_qty, avg_price,
    filled_ms, sl_price, tp_price``. ``results``: execution_results rows with ``execution_order_id,
    close_type, realized_pnl, fee, closed_ms``. ``finances``: execution_finances keyed by execution_order_id
    with ``net_pnl, gross_pnl, roi_pct, is_fully_closed``. ``signals``: strategy_signals rows with ``event_id,
    symbol, combo, side, signal_ms, entry, tp, sl``. ``errors``: control_logs rows with ``layer, symbol,
    error_code, message, time_ms, discord_status``. ``candle_last_close``: symbol -> last closed candle close_time ms.
    """
    sym_f = symbol.upper().strip()
    combo_f = combo.strip().upper()

    def keep(sym: str, cmb: str) -> bool:
        return (not sym_f or sym.upper() == sym_f) and (not combo_f or cmb.upper() == combo_f)

    results_by_order: dict[int, list[dict]] = defaultdict(list)
    for r in results:
        results_by_order[int(r["execution_order_id"])].append(r)

    closed: list[dict] = []
    open_positions: list[dict] = []
    for o in orders:
        if not keep(o["symbol"], o["combo"]):
            continue
        oid = int(o["id"])
        fin = finances.get(oid, {})
        rows = results_by_order.get(oid, [])
        is_closed = bool(fin.get("is_fully_closed"))
        if is_closed:
            net = _opt(fin.get("net_pnl"))
            if net is None:
                net = sum(_f(r.get("realized_pnl")) - _f(r.get("fee")) for r in rows)
            close_ms = max((r.get("closed_ms") or 0 for r in rows), default=None)
            if not _window_ok(close_ms or o.get("filled_ms"), now_ms, days):
                continue
            closed.append({
                "id": oid, "event_id": o.get("event_id", ""), "symbol": o["symbol"], "combo": o["combo"],
                "side": o["side"], "entry_ms": o.get("filled_ms"), "exit_ms": close_ms,
                "close_type": ",".join(sorted({str(r.get("close_type") or "") for r in rows if r.get("close_type")})),
                "net_pnl": round(net, 8), "roi_pct": _opt(fin.get("roi_pct")),
            })
        elif _f(o.get("executed_qty")) > 0:
            open_positions.append({
                "id": oid, "event_id": o.get("event_id", ""), "symbol": o["symbol"], "combo": o["combo"],
                "side": o["side"], "entry_ms": o.get("filled_ms"), "avg_price": _opt(o.get("avg_price")),
                "qty": _f(o.get("executed_qty")), "sl": _opt(o.get("sl_price")), "tp": _opt(o.get("tp_price")),
            })

    closed.sort(key=lambda t: t["exit_ms"] or 0, reverse=True)
    wins = [t for t in closed if t["net_pnl"] > 0]
    losses = [t for t in closed if t["net_pnl"] < 0]
    gross_pnl = sum(_f(finances.get(t["id"], {}).get("gross_pnl")) for t in closed)
    net_pnl = sum(t["net_pnl"] for t in closed)
    rois = [t["roi_pct"] for t in closed if t["roi_pct"] is not None]

    by_combo_map: dict[tuple[str, str], dict] = {}
    for t in closed:
        key = (t["symbol"], t["combo"])
        row = by_combo_map.setdefault(key, {"symbol": key[0], "combo": key[1], "trades": 0, "wins": 0,
                                            "net_pnl": 0.0})
        row["trades"] += 1
        row["wins"] += 1 if t["net_pnl"] > 0 else 0
        row["net_pnl"] = round(row["net_pnl"] + t["net_pnl"], 8)
    by_combo = sorted(by_combo_map.values(), key=lambda r: r["net_pnl"], reverse=True)
    for row in by_combo:
        row["win_rate_pct"] = round(100.0 * row["wins"] / row["trades"], 2) if row["trades"] else None

    signal_rows = [s for s in signals if keep(s["symbol"], s["combo"]) and _window_ok(s.get("signal_ms"), now_ms, days)]
    error_rows = [e for e in errors if _window_ok(e.get("time_ms"), now_ms, 1)]

    summary = {
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100.0 * len(wins) / len(closed), 2) if closed else None,
        "gross_pnl": round(gross_pnl, 8),
        "net_pnl": round(net_pnl, 8),
        "avg_roi_pct": round(sum(rois) / len(rois), 4) if rois else None,
        "open_positions": len(open_positions),
        "signals_in_window": len(signal_rows),
        "last_signal_ms": max((s["signal_ms"] for s in signal_rows if s.get("signal_ms")), default=None),
        "errors_last_24h": len(error_rows),
    }

    def public_or_full(rows: list[dict], fields: tuple[str, ...]) -> list[dict]:
        if not public:
            return rows
        return [{k: r.get(k) for k in fields} for r in rows]

    health = {
        "candles": [{"symbol": s, "last_close_ms": ms} for s, ms in sorted(candle_last_close.items())],
        "errors_last_24h": [
            {k: e.get(k) for k in PUBLIC_ERROR_FIELDS} if public else
            {"layer": e.get("layer"), "symbol": e.get("symbol"), "error_code": e.get("error_code"),
             "message": e.get("message"), "time_ms": e.get("time_ms"), "discord_status": e.get("discord_status")}
            for e in error_rows[:50]
        ],
    }

    trades_out = public_or_full(closed[:200], PUBLIC_TRADE_FIELDS)
    open_out = public_or_full(open_positions, PUBLIC_OPEN_FIELDS)
    signals_out = public_or_full(signal_rows[:50], PUBLIC_SIGNAL_FIELDS)

    return {
        "schema": "autotrade-v3-live-terminal-1",
        "generated_ms": now_ms,
        "filters": {"symbol": sym_f, "combo": combo_f, "days": days},
        "public": public,
        "summary": summary,
        "by_combo": by_combo,
        "trades": trades_out,
        "open_positions": open_out,
        "signals": signals_out,
        "health": health,
    }
