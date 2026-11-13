"""MOCK BingX / Discord fakes for V3 offline tests. Nothing here is live evidence."""
from __future__ import annotations

import itertools

from autotrade_v3.live.config import V3Settings
from autotrade_v3.live.l3_execution import L3Executor, V3Signal
from autotrade_v3.live.l4_control import ControlLog, L4Reader
from autotrade_v3.live.l5_alert import L5Alert
from autotrade_v3.live.store import MemoryStore
from autotrade_v3.live.transport import BingXApiError, SpacingGuard, V3BingXClient

PRICE = {"BTC-USDT": 100000.0, "ETH-USDT": 3000.0}


def settings(**kw) -> V3Settings:
    base = dict(symbols=("BTC-USDT", "ETH-USDT"), bingx_api_key="mock-key", bingx_api_secret="mock-secret",
                database_url="", discord_enabled=True, discord_webhook_btc="https://mock.local/btc",
                discord_webhook_eth="https://mock.local/eth", discord_webhook_error="https://mock.local/error",
                tp_trailing_split_enabled=True, trailing_activation_pct=0.005, trailing_callback_rate=0.005)
    base.update(kw)
    return V3Settings(**base)


class FakeBingX:
    """MOCK exchange. ``entry_mode``: ok | lost | lost_absent | reject | never_fills.
    ``leg_mode[type]``: ok | reject | lost | lost_absent. ``get_mode``: ok | error."""

    fmt_price = staticmethod(V3BingXClient.fmt_price)
    fmt_qty = staticmethod(V3BingXClient.fmt_qty)

    def __init__(self):
        self.ids = itertools.count(9000001)
        self.orders: dict[str, dict] = {}
        self.by_client: dict[str, str] = {}
        self.entry_mode = "ok"
        self.leg_mode: dict[str, str] = {}
        self.get_mode = "ok"
        self.entry_posts: list[dict] = []
        self.order_posts: list[dict] = []
        self.gets: list[dict] = []
        self.position_gets: list[str] = []
        self.guard = SpacingGuard(min_interval_sec=0)

    def _create(self, params: dict, status: str) -> dict:
        oid = str(next(self.ids))
        order = {"orderId": oid, "symbol": params["symbol"], "side": params["side"],
                 "positionSide": params.get("positionSide"), "type": params["type"], "status": status,
                 "clientOrderId": params.get("clientOrderId", ""), "origQty": params.get("quantity"),
                 "executedQty": params.get("quantity") if status == "FILLED" else "0",
                 "avgPrice": str(PRICE[params["symbol"]]) if status == "FILLED" else "0",
                 "stopPrice": params.get("stopPrice", ""), "activationPrice": "", "priceRate": "",
                 "updateTime": 1763000000000}
        self.orders[oid] = order
        if params.get("clientOrderId"):
            self.by_client[params["clientOrderId"]] = oid
        return order

    def place_market_entry(self, symbol, side, qty, leverage, client_order_id=None):
        params = {"symbol": symbol, "side": side, "positionSide": "LONG" if side == "BUY" else "SHORT",
                  "type": "MARKET", "quantity": V3BingXClient.fmt_qty(qty), "clientOrderId": client_order_id,
                  "leverage": leverage}
        self.entry_posts.append(params)
        mode = self.entry_mode
        if mode == "reject":
            raise BingXApiError("101204", "Insufficient margin", "/openApi/swap/v2/trade/order", params)
        if mode == "lost_absent":
            raise RuntimeError("BingX trade transport failed: ReadTimeout")
        order = self._create(params, "NEW" if mode == "never_fills" else "FILLED")
        if mode == "lost":
            raise RuntimeError("BingX trade transport failed: ReadTimeout")
        return {"code": 0, "data": {"order": {"orderId": order["orderId"], "clientOrderId": client_order_id}}}

    def place_order(self, params):
        self.order_posts.append(dict(params))
        mode = self.leg_mode.get(params["type"], "ok")
        if mode == "reject":
            raise BingXApiError("109400", f"{params['type']} rejected (mock)", "/openApi/swap/v2/trade/order", params)
        if mode == "lost_absent":
            raise RuntimeError("BingX trade transport failed: ConnectError")
        order = self._create(params, "FILLED" if params["type"] == "MARKET" else "NEW")
        if params["type"] == "TRAILING_STOP_MARKET":
            order["activationPrice"] = ""   # BingX GET often omits it; ACK carries it
        if mode == "lost":
            raise RuntimeError("BingX trade transport failed: ConnectError")
        ack = {"orderId": order["orderId"], "symbol": params["symbol"], "side": params["side"],
               "positionSide": params["positionSide"], "type": params["type"],
               "activationPrice": params.get("activationPrice", ""), "priceRate": params.get("priceRate", ""),
               "stopPrice": params.get("stopPrice", ""), "clientOrderId": params.get("clientOrderId")}
        return {"code": 0, "data": {"order": ack}}

    def get_order_detail(self, symbol, order_id=None, client_order_id=None):
        self.gets.append({"symbol": symbol, "orderId": order_id, "clientOrderId": client_order_id})
        if self.get_mode == "error":
            raise RuntimeError("BingX trade transport failed: ReadTimeout")
        oid = order_id or self.by_client.get(client_order_id or "")
        if not oid or oid not in self.orders:
            raise BingXApiError("109421", "order not exist", "/openApi/swap/v2/trade/order", {})
        return {"code": 0, "data": {"order": dict(self.orders[oid])}}

    def get_positions(self, symbol):
        self.position_gets.append(symbol)
        return [{"symbol": symbol, "positionSide": "LONG", "positionAmt": "0.001"}]

    def get_contract_rules(self, symbol):
        return {"symbol": symbol, "quantityPrecision": 4, "tradeMinQuantity": "0.0001", "tradeMinUSDT": "2",
                "pricePrecision": 1}


class RecordingSender:
    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def __call__(self, url, content):
        self.calls.append((url, content))
        if self.fail:
            raise RuntimeError("Discord unreachable (mock)")
        return True, "HTTP 204"

    def channels(self):
        return [url.rsplit("/", 1)[-1] for url, _ in self.calls]


def build(store=None, client=None, sender=None, late_verify_attempts=0, **settings_kw):
    s = settings(**settings_kw)
    store = store or MemoryStore()
    client = client or FakeBingX()
    sender = sender or RecordingSender()
    alert = L5Alert(enabled=True, webhook_btc=s.discord_webhook_btc, webhook_eth=s.discord_webhook_eth,
                    webhook_error=s.discord_webhook_error, store=store, sender=sender, synchronous=True)
    control = ControlLog(store, alert)
    reader = L4Reader(client, store, control, confirm_attempts=3, confirm_sleep_sec=0, background=False)
    executor = L3Executor(s, client, store, reader, control, alert, protection_confirm_attempts=2,
                          protection_confirm_sleep_sec=0, late_verify_attempts=late_verify_attempts,
                          late_verify_sleep_sec=0)
    return s, store, client, sender, alert, control, reader, executor


def sig(symbol="BTC-USDT", combo=1, side="BUY", close_time=1763000099999) -> V3Signal:
    entry = PRICE[symbol]
    name = "TIER" if combo == 11 else (f"C{combo - 90}" if combo > 100 else f"C{combo}")
    tf = "1h" if combo in (1, 2, 3, 4, 6, 11) or combo > 100 else "15m"
    tp = entry * (1.02 if side == "BUY" else 0.98)
    sl = entry * (0.992 if side == "BUY" else 1.008)
    return V3Signal(event_id=f"{symbol}|{tf}|{name}|{side}|{close_time}", symbol=symbol, combo=combo, combo_name=name,
                    side=side, timeframe=tf, close_time=close_time, entry=entry, tp=tp, sl=sl)
