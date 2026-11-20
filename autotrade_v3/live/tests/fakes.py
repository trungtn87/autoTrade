"""MOCK BingX / Discord fakes for V3 offline tests. Nothing here is live evidence."""
from __future__ import annotations

import itertools
from decimal import Decimal, ROUND_DOWN

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
                )
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
        self.qty_multiplier = 1   # MOCK only: lets a legacy split fixture get a splittable size
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
        qty = Decimal(str(qty)) * self.qty_multiplier
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
        if not oid or oid not in self.orders or self.orders[oid].get("_hidden"):
            raise BingXApiError("109421", "order not exist", "/openApi/swap/v2/trade/order", {})
        return {"code": 0, "data": {"order": dict(self.orders[oid])}}

    # ---- cleanup support (MOCK) ---------------------------------------------
    COND = ("STOP_MARKET", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET")

    def trigger_fill(self, order_id, qty=None, price="66000", parent="gone"):
        """Simulate BingX triggering a conditional order: a FILLED child with triggerOrderId=parent."""
        o = self.orders[order_id]
        q = qty or o["origQty"]
        child_id = str(next(self.ids))
        self.orders[child_id] = {"orderId": child_id, "symbol": o["symbol"], "side": o["side"], "positionSide": o["positionSide"],
                                 "type": o["type"], "status": "FILLED", "origQty": q, "executedQty": q, "avgPrice": str(price),
                                 "triggerOrderId": order_id, "clientOrderId": "", "updateTime": 1763000100000}
        if parent == "gone":
            o["_hidden"] = True
        else:
            o["status"], o["executedQty"] = "FILLED", "0"
        return child_id

    def auto_cancel(self, order_id):
        self.orders[order_id]["status"] = "CANCELED"

    def get_open_orders(self, symbol):
        self.open_gets = getattr(self, "open_gets", 0) + 1
        if getattr(self, "open_mode", "ok") == "error":
            raise RuntimeError("BingX trade transport failed: ReadTimeout")
        hide = getattr(self, "open_hides", ())
        return [dict(o) for o in self.orders.values() if o["symbol"] == symbol and o["type"] in self.COND
                and o["status"] in ("NEW", "PARTIALLY_FILLED") and not o.get("_hidden") and o["type"] not in hide]

    def cancel_order(self, symbol, order_id=None, client_order_id=None):
        self.cancels = getattr(self, "cancels", [])
        self.cancels.append(str(order_id))
        mode = getattr(self, "cancel_mode", "ok")
        o = self.orders.get(str(order_id))
        if mode == "reject" or o is None or o["status"] != "NEW":
            raise BingXApiError("109421", "order not exist", "/openApi/swap/v2/trade/order", {})
        if mode == "lost_not_applied":
            raise RuntimeError("BingX trade transport failed: ReadTimeout")
        o["status"] = "CANCELED"
        if mode == "lost_applied":
            raise RuntimeError("BingX trade transport failed: ReadTimeout")
        return {"code": 0, "data": {"order": {"orderId": str(order_id), "status": "CANCELED"}}}

    def amend_order(self, symbol, *, order_id, quantity):
        """Mock in-place quantity-only amend. No deletion or new order is created."""
        self.amends = getattr(self, "amends", [])
        self.amends.append((str(order_id), str(quantity)))
        row = self.orders.get(str(order_id))
        mode = getattr(self, "amend_mode", "ok")
        if mode == "reject" or row is None or row["symbol"] != symbol or row["type"] != "STOP_MARKET" or row["status"] != "NEW":
            raise BingXApiError("109400", "amend rejected (mock)", "/openApi/swap/v1/trade/amend", {})
        if mode == "lost_not_applied":
            raise RuntimeError("BingX amend response lost before applying")
        if mode == "triggered_before":
            row["status"] = "FILLED"
            row["executedQty"] = row["origQty"]
            raise RuntimeError("SL triggered during amend (mock)")
        row["origQty"] = str(quantity)
        if mode == "lost_applied":
            raise RuntimeError("BingX amend response lost after applying")
        return {"code": 0, "data": {"orderId": str(order_id), "quantity": str(quantity)}}

    def get_order_history(self, symbol, start_ms, end_ms, required_ids=None):
        if getattr(self, "history_mode", "ok") == "error":
            raise ValueError("BingX history missing/truncated")
        return [dict(o) for o in self.orders.values() if o["symbol"] == symbol and not o.get("_hidden")]

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
    # TP/SL pct come from the default registry spec, as in production (L2 always attaches them).
    from autotrade_v3.live.strategy_registry import DEFAULT_REGISTRY, load_registry
    spec = load_registry(DEFAULT_REGISTRY).get(symbol, combo)
    pct = {} if spec is None else {"tp_pct": spec.tp_pct, "sl_pct": spec.sl_pct}
    return V3Signal(event_id=f"{symbol}|{tf}|{name}|{side}|{close_time}", symbol=symbol, combo=combo, combo_name=name,
                    side=side, timeframe=tf, close_time=close_time, entry=entry, tp=tp, sl=sl, **pct)


def open_legacy_split(executor, sg=None):
    """MOCK fixture for a position opened BEFORE the FIXED_TP_SL change: SL full, TP ~50%, trailing ~50%.

    New entries never produce this shape any more; this only exists so the L4 cleanup of old
    positions (still open on BingX at deploy time) keeps being tested.
    """
    client, store = executor.client, executor.store
    sg = sg or sig()
    client.qty_multiplier = 4          # 4 steps of 0.001 BTC: enough for two valid legs
    try:
        r = executor.execute(sg)
    finally:
        client.qty_multiplier = 1
    assert r["stage"] == "protected"
    ex = store.executions[r["event_id"]]
    q = Decimal(str(ex["executed_qty"]))
    step = Decimal(1).scaleb(q.as_tuple().exponent)
    half = (q / 2).quantize(step, rounding=ROUND_DOWN)
    other = q - half
    client.orders[ex["tp_order_id"]]["origQty"] = str(half)
    opposite = "SELL" if sg.side == "BUY" else "BUY"
    params = {"symbol": sg.symbol, "side": opposite, "positionSide": "LONG" if sg.side == "BUY" else "SHORT",
              "type": "TRAILING_STOP_MARKET", "quantity": str(other),
              "activationPrice": str(round(ex["avg_price"] * (1.005 if sg.side == "BUY" else 0.995), 2)),
              "priceRate": "0.005", "clientOrderId": "legacy-trail-" + sg.event_id[-13:]}
    ack = client.place_order(params)["data"]["order"]
    fields = {"tp_qty": str(half), "exit_mode": "split_trailing", "trailing_order_id": ack["orderId"],
              "trailing_qty": str(other), "trailing_status": "NEW", "trailing_callback_rate": 0.005}
    executor._save(sg, fields)
    return store.executions[r["event_id"]]
