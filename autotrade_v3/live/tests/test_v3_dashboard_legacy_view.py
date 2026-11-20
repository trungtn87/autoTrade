"""The V2 Dashboard UI reads the V2 response shape; the V3 adapter must produce it from V3 data."""
from __future__ import annotations

from autotrade_v3.dashboard.legacy_view import to_legacy


def _payload():
    return {
        "summary": {"closed_trades": 2},
        "by_combo": [{"symbol": "ETH-USDT", "combo": "C3", "trades": 2, "wins": 1,
                      "net_pnl": 0.5, "win_rate_pct": 50.0}],
        "trades": [
            {"id": 1, "symbol": "ETH-USDT", "combo": "C3", "side": "BUY", "entry_ms": 1000,
             "exit_ms": 5000, "close_type": "TP,SL", "net_pnl": 2.0, "roi_pct": 4.0},
            {"id": 2, "symbol": "ETH-USDT", "combo": "C3", "side": "SELL", "entry_ms": 2000,
             "exit_ms": 6000, "close_type": "SL", "net_pnl": -1.5, "roi_pct": None},
        ],
        "open_positions": [{"id": 3, "symbol": "ETH-USDT", "combo": "C3", "side": "BUY",
                            "entry_ms": 7000, "qty": 1.0}],
    }


def test_shape_matches_what_v2_ui_reads() -> None:
    out = to_legacy(_payload(), {"ETH-USDT": 9000})
    assert set(out) >= {"theory", "last_sync", "summary", "unresolved"}
    combo = out["theory"]["combos"][0]
    assert (combo["total"], combo["closed"], combo["unresolved"]) == (3, 2, 1)
    assert (combo["win"], combo["loss"], combo["win_rate"]) == (1, 1, 50.0)
    assert combo["real_roi_total"] is None  # no margin in V3 data: not guessed
    assert out["unresolved"] == 1 and out["summary"]["closed"] == 2


def test_history_outcomes_and_open_rows() -> None:
    hist = to_legacy(_payload(), {})["theory"]["history"]
    by_entry = {h["entry_ms"]: h for h in hist}
    assert by_entry[1000]["outcome"] == "WIN" and by_entry[1000]["pnl"] == 2.0
    assert by_entry[1000]["exit_types"] == ["TP", "SL"]
    assert by_entry[2000]["outcome"] == "LOSS" and by_entry[2000]["roi_pct"] is None
    assert by_entry[7000]["status"] == "open" and by_entry[7000]["result"] == "UNRESOLVED"


def test_last_sync_is_candle_time_and_absent_without_candles() -> None:
    out = to_legacy(_payload(), {"ETH-USDT": 1_700_000_000_000})
    assert out["last_sync"]["status"] == "success"
    assert out["last_sync"]["source"] == "candle_close"
    assert to_legacy(_payload(), {})["last_sync"] is None
