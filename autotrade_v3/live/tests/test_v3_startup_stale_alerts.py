"""Startup notice (ERROR channel) and first-miss candle_stale alert."""
from autotrade_v3.live.l5_alert import L5Alert


def _alert(sent):
    def sender(url, content):
        sent.append((url, content))
        return True, "ok"
    return L5Alert(enabled=True, webhook_btc="btc", webhook_eth="eth", webhook_error="err",
                   sender=sender, synchronous=True)


def test_startup_notice_goes_to_error_channel_without_secrets():
    sent = []
    a = _alert(sent)
    a.notice("Engine V3 khởi động", ["Symbols: BTC-USDT, ETH-USDT", "Execution: bật"])
    assert len(sent) == 1
    url, content = sent[0]
    assert url == "err"
    assert "Engine V3 khởi động" in content
    assert "secret" not in content.lower()
    assert a.sent[-1]["kind"] == "notice" and a.sent[-1]["status"] == "sent"


def test_notice_disabled_when_discord_off():
    sent = []
    a = L5Alert(enabled=False, webhook_btc="", webhook_eth="", webhook_error="", sender=lambda u, c: (True, "x"),
                synchronous=True)
    a.notice("x", ["y"])
    assert sent == [] and a.sent[-1]["status"] == "disabled"
