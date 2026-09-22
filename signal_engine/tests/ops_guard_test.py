from __future__ import annotations

from app.config import Settings
from app.discord_diag import build_discord_scan_alert
from app.warmup_seed import INTERVAL_15M_MS, load_warmup_seed


def test_seed():
    for symbol in ("BTC-USDT", "ETH-USDT"):
        d = load_warmup_seed(symbol, 12000)
        assert len(d) == 12000, (symbol, len(d))
        assert d["open_time"].is_unique
        assert bool((d["open_time"].diff().dropna() == INTERVAL_15M_MS).all())
        assert bool((d["close_time"] == d["open_time"] + INTERVAL_15M_MS - 1).all())


def test_discord_guard():
    settings = Settings()
    ready = {
        "status": "ok",
        "symbols": {
            symbol: {
                "cached_15m": 12000,
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
        "symbols": {
            symbol: {
                "cached_15m": 9713,
                "data_validation": {"ok": True},
                "combo_readiness": {"ready": [], "skipped": [1, 2]},
            }
            for symbol in settings.symbols
        },
    }
    content, signature = build_discord_scan_alert(settings, broken)
    assert "FINAL14 AUTOTRADE ALERT" in content
    assert "NOT READY" in content
    assert "9713" in content
    assert "not_ready" in signature


def main():
    test_seed()
    test_discord_guard()
    print("OPS GUARD TEST PASS")


if __name__ == "__main__":
    main()
