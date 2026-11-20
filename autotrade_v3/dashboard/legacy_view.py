"""Adapter: V3 Live Terminal payload -> the response shape the existing V2 Dashboard UI reads.

The V2 UI (static/app.js, pnl_chart.js) keeps its layout. Only the data source changes: it reads
``theory.combos``, ``theory.history``, ``last_sync``, ``summary`` and ``unresolved`` from /api/dashboard.
Fields the V3 data does not have are returned as None, never guessed (e.g. ROI weighted by margin).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _iso_ms(ms: int | None) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()


def _outcome(pnl: float | None) -> str | None:
    if pnl is None:
        return None
    if pnl > 0:
        return "WIN"
    if pnl < 0:
        return "LOSS"
    return "BREAKEVEN"


def to_legacy(payload: dict[str, Any], candle_last_close: dict[str, int]) -> dict[str, Any]:
    """``payload`` is a build_payload() result (non-public). Returns the V2 UI shape."""
    trades = payload.get("trades", [])
    open_positions = payload.get("open_positions", [])
    by_combo = payload.get("by_combo", [])

    history: list[dict[str, Any]] = []
    for t in trades:
        history.append({
            "symbol": t["symbol"], "combo": t["combo"], "side": t["side"],
            "status": "closed", "entry_ms": t.get("entry_ms"), "exit_ms": t.get("exit_ms"),
            "outcome": _outcome(t.get("net_pnl")), "result": _outcome(t.get("net_pnl")),
            "roi_pct": t.get("roi_pct"), "recorded_roi_pct": t.get("roi_pct"),
            "pnl": t.get("net_pnl"), "margin": None,
            "source": "v3_execution_finances", "reason": None,
            "entry_qty": None, "matched_exit_qty": None, "shared_position": False,
            "exit_types": [x for x in str(t.get("close_type") or "").split(",") if x],
        })
    for p in open_positions:
        history.append({
            "symbol": p["symbol"], "combo": p["combo"], "side": p["side"],
            "status": "open", "entry_ms": p.get("entry_ms"), "exit_ms": None,
            "outcome": None, "result": "UNRESOLVED", "roi_pct": None, "recorded_roi_pct": None,
            "pnl": None, "margin": None, "source": "v3_execution_orders", "reason": "open",
            "entry_qty": p.get("qty"), "matched_exit_qty": None, "shared_position": False,
            "exit_types": [],
        })

    open_count: dict[tuple[str, str], int] = {}
    for p in open_positions:
        key = (p["symbol"], p["combo"])
        open_count[key] = open_count.get(key, 0) + 1

    combos: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in by_combo:
        key = (row["symbol"], row["combo"])
        seen.add(key)
        closed = int(row["trades"])
        wins = int(row["wins"])
        unresolved = open_count.get(key, 0)
        roi_rows = [t for t in trades if t["symbol"] == key[0] and t["combo"] == key[1]
                    and t.get("roi_pct") is not None]
        combos.append(_combo(key, closed, wins, unresolved, row.get("win_rate_pct"),
                             row.get("net_pnl"), len(roi_rows)))
    for key, unresolved in open_count.items():
        if key not in seen:
            combos.append(_combo(key, 0, 0, unresolved, None, None, 0))
    combos.sort(key=lambda c: (c["symbol"] or "", str(c["combo"] or "")))

    last_close = max(candle_last_close.values(), default=None)
    return {
        "theory": {
            "available": True,
            "reason": None,
            "combos": combos,
            "history": sorted(history, key=lambda h: h["exit_ms"] or h["entry_ms"] or 0, reverse=True),
        },
        # The V2 UI reads this as "last successful sync". V3 has no sync record; the newest
        # 15m candle close is the closest real timestamp, so it is labelled as such in the report.
        "last_sync": (None if last_close is None
                      else {"status": "success", "time": _iso_ms(last_close), "source": "candle_close"}),
        "trades": [],
        "summary": {"closed": int(payload.get("summary", {}).get("closed_trades", len(trades))),
                    "total": len(history)},
        "unresolved": len(open_positions),
    }


def _combo(key, closed, wins, unresolved, win_rate, pnl_total, roi_count):
    losses = closed - wins
    return {
        "symbol": key[0], "combo": key[1],
        "total": closed + unresolved, "closed": closed, "unresolved": unresolved,
        "win": wins, "loss": losses, "tp_count": wins, "sl_count": losses,
        "classified": closed, "win_rate": win_rate,
        "real_roi_total": None, "roi_estimated": False, "estimated_count": 0,
        "roi_coverage": roi_count, "pnl_total": pnl_total,
        "pnl_coverage": closed,
    }
