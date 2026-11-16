"""L5 merges repeated errors (same layer, step, symbol, error code) for one window.

MOCK sender only: nothing is sent to Discord.
"""
from autotrade_v3.live.l5_alert import L5Alert


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _alert(clock, sent):
    def sender(url, content):
        sent.append(content)
        return True, "ok"
    return L5Alert(enabled=True, webhook_btc="https://x/btc", webhook_eth="https://x/eth",
                   webhook_error="https://x/err", sender=sender, synchronous=True,
                   error_window_sec=3600.0, clock=clock)


def _rec(code="100410", step="eth_c1_h1", msg="đợt 6/12 lỗi"):
    return {"layer": "L2", "step": step, "message": msg, "symbol": "ETH-USDT", "error_code": code}


def test_first_error_sent_then_repeats_merged_until_window_ends():
    clock, sent = Clock(), []
    alert = _alert(clock, sent)
    alert.error(_rec(), 1)                       # first: sent at once
    for i in range(5):                           # repeats inside the hour: not sent
        clock.t = 60.0 * (i + 1)
        alert.error(_rec(msg=f"đợt {i}/12 lỗi"), i + 2)
    assert len(sent) == 1
    clock.t = 3601.0                             # window over: next one is sent with the merged count
    alert.error(_rec(), 9)
    assert len(sent) == 2
    assert "Đã gộp 5 cảnh báo" in sent[1]
    assert "Mã lỗi: `100410`" in sent[0]


def test_changed_error_code_is_sent_at_once():
    clock, sent = Clock(), []
    alert = _alert(clock, sent)
    alert.error(_rec(code="100410"), 1)
    clock.t = 60.0
    alert.error(_rec(code="100001"), 2)          # different code = different key
    assert len(sent) == 2
    assert "100001" in sent[1]


def test_different_step_or_symbol_is_not_merged():
    clock, sent = Clock(), []
    alert = _alert(clock, sent)
    alert.error(_rec(step="eth_c1_h1"), 1)
    alert.error(_rec(step="strategy_calculate"), 2)
    alert.error({**_rec(), "symbol": "BTC-USDT"}, 3)
    assert len(sent) == 3


def test_no_error_code_repeats_are_also_merged():
    clock, sent = Clock(), []
    alert = _alert(clock, sent)
    for _ in range(3):
        alert.error(_rec(code=None), None)
    assert len(sent) == 1
