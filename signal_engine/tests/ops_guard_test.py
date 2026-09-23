from __future__ import annotations

import tempfile
from pathlib import Path

from app.config import Settings
from app.discord_diag import build_discord_scan_alert
from app.state import SignalState
from app.warmup_seed import INTERVAL_15M_MS, load_warmup_seed


def test_seed():
    for symbol in ("BTC-USDT", "ETH-USDT"):
        d = load_warmup_seed(symbol, 3400)
        assert len(d) == 3400, (symbol, len(d))
        assert d["open_time"].is_unique
        assert bool((d["open_time"].diff().dropna() == INTERVAL_15M_MS).all())
        assert bool((d["close_time"] == d["open_time"] + INTERVAL_15M_MS - 1).all())


def test_discord_guard():
    settings = Settings()
    ready = {
        "status": "ok",
        "symbols": {
            symbol: {
                "cached_15m": 3400,
                "data_validation": {"ok": True},
                "combo_readiness": {"ready": [1], "skipped": []},
            }
            for symbol in settings.symbols
        },
    }
    content, signature = build_discord_scan_alert(settings, ready)
    assert content == ""
    assert signature == ""

    broken = {
        "status": "ok",
        "bingx_api_error_diag_15m": {
            "109425_count": 2,
            "109425_by_source": {"market": 1, "executor": 1},
        },
        "symbols": {
            symbol: {
                "cached_15m": 3200,
                "data_validation": {"ok": True},
                "combo_readiness": {"ready": [], "skipped": [1, 2]},
            }
            for symbol in settings.symbols
        },
    }
    content, signature = build_discord_scan_alert(settings, broken)
    assert "FINAL14 AUTOTRADE ALERT" in content
    assert "NOT READY" in content
    assert "3200" in content
    assert "Local BingX 109425 / 15m: 2" in content
    assert "not_ready" in signature
    assert "bingx109425:2:1:1" in signature


def test_bingx_error_ledger():
    now_ms = 2_000_000_000_000
    with tempfile.TemporaryDirectory() as tmp:
        state = SignalState(str(Path(tmp) / "state.db"))
        state.record_bingx_api_error(
            source="market",
            endpoint="/openApi/swap/v3/quote/klines",
            symbol="BTC-USDT",
            code=109425,
            message="bad pair",
            at_ms=now_ms - 10_000,
        )
        state.record_bingx_api_error(
            source="executor",
            endpoint="/openApi/swap/v2/trade/order",
            symbol="ETH-USDT",
            code="109425",
            message="bad pair",
            at_ms=now_ms - 5_000,
        )
        state.record_bingx_api_error(
            source="market",
            endpoint="/openApi/swap/v3/quote/klines",
            symbol="ETH-USDT",
            code=109429,
            message="blocked",
            at_ms=now_ms,
        )

        diag = state.bingx_api_error_summary(
            window_ms=15 * 60 * 1000,
            now_ms=now_ms,
        )
        assert diag["109425_count"] == 2
        assert diag["109425_by_source"]["market"] == 1
        assert diag["109425_by_source"]["executor"] == 1
        assert diag["code_counts"]["109429"] == 1
        assert len(diag["recent_109425"]) == 2


def main():
    test_seed()
    test_discord_guard()
    test_bingx_error_ledger()
    print("OPS GUARD TEST PASS")


if __name__ == "__main__":
    main()
