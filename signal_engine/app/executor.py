from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import time
from datetime import datetime, timezone

import httpx

from .config import Settings
from .bingx_market import BingXMarketClient
from .strategy import Signal

log = logging.getLogger(__name__)


def execution_prices(signal: Signal, settings: Settings) -> tuple[float, float, float]:
    """Use the locked FINAL14 strategy levels without legacy price transforms."""
    return float(signal.entry), float(signal.tp), float(signal.sl)


class Executor:
    def __init__(self, settings: Settings, api_error_recorder=None, request_guard=None):
        self.settings = settings
        self.client = httpx.Client(timeout=20.0)
        self._contract_cache: dict[str, dict] = {}
        self.api_error_recorder = api_error_recorder
        self.request_guard = request_guard or BingXMarketClient(
            base_url=settings.bingx_base_url,
            api_key=settings.bingx_api_key,
            api_secret=settings.bingx_api_secret,
            error_recorder=api_error_recorder,
        )

    def targets(self) -> list[tuple[str, str, float]]:
        """One live BingX account using the configured API credentials directly."""
        if not (self.settings.bingx_api_key and self.settings.bingx_api_secret):
            return []
        return [(
            "bingx_account",
            "direct://bingx",
            self.settings.order_margin_usdt * self.settings.leverage,
        )]

    def discord_url(self, symbol: str) -> str:
        symbol = symbol.upper()
        if symbol == "BTC-USDT" and self.settings.discord_webhook_btc:
            return self.settings.discord_webhook_btc
        if symbol == "ETH-USDT" and self.settings.discord_webhook_eth:
            return self.settings.discord_webhook_eth
        return ""

    def discord_allowed(self) -> bool:
        if not self.settings.discord_enabled:
            return False
        if self.settings.dry_run and not self.settings.discord_on_dry_run:
            return False
        return True

    @staticmethod
    def _smc_text(smc_dir: int, side: str) -> str:
        direction = 1 if side == "BUY" else -1
        if smc_dir == direction:
            return "ALIGN"
        if smc_dir == 0:
            return "NEUTRAL"
        return "VETO"

    def build_discord_message(self, signal: Signal) -> str:
        entry, tp, sl = execution_prices(signal, self.settings)
        icon = "🟢" if signal.side == "BUY" else "🔴"
        mode = "🧪 DRY RUN" if self.settings.dry_run else "🚀 LIVE"
        close_utc = datetime.fromtimestamp(
            signal.close_time / 1000.0, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
        return (
            f"{icon} **{signal.symbol} | Combo {signal.combo} {signal.side}**\n"
            f"TF: `{signal.timeframe}`\n"
            f"Entry: `{entry}`\n"
            f"🎯 TP: `{tp}`\n"
            f"🛡️ SL: `{sl}`\n"
            f"SMC: `{self._smc_text(signal.smc_dir, signal.side)}`\n"
            f"Mode: **{mode}**\n"
            f"Close: `{close_utc}`"
        )

    def send_discord(self, signal: Signal) -> dict:
        url = self.discord_url(signal.symbol)
        target = f"discord:{signal.symbol}"
        if not url:
            return {"target": target, "ok": False, "skipped": True, "reason": "no_webhook"}
        if not self.discord_allowed():
            return {"target": target, "ok": False, "skipped": True, "reason": "disabled"}

        payload = {"content": self.build_discord_message(signal)}
        for attempt in range(1, 4):
            try:
                r = self.client.post(url, json=payload)
                if r.status_code in (200, 204):
                    log.info("Discord %s sent", signal.symbol)
                    return {"target": target, "ok": True, "status_code": r.status_code}
                if r.status_code == 429:
                    wait = 2.0
                    try:
                        body = r.json()
                        retry_after = float(body.get("retry_after", 2))
                        wait = retry_after / 1000.0 if retry_after > 10 else retry_after
                    except Exception:
                        pass
                    time.sleep(min(max(wait, 0.5), 10.0))
                    continue
                log.warning("Discord %s failed status=%s body=%s", signal.symbol, r.status_code, r.text[:300])
                time.sleep(1.0)
            except Exception as exc:
                log.warning("Discord %s attempt %s failed: %s", signal.symbol, attempt, exc)
                time.sleep(1.0)

        log.error("DISCORD_SIGNAL_SEND_FAILED symbol=%s", signal.symbol)
        return {"target": target, "ok": False, "error": "discord_send_failed"}

    def build_payload(self, signal: Signal, usdt_amount: float) -> dict:
        entry, tp, sl = execution_prices(signal, self.settings)
        return {
            "signal_id": signal.event_id,
            "combo": f"Combo {signal.combo}",
            "side": signal.side,
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "symbol": signal.symbol,
            "timeframe": signal.timeframe,
            "order_type": "MARKET",
            "usdt_amount": usdt_amount,
            "source": "bingx-final14-hardtp",
            "exit_mode": "100pct_hard_tp_sl",
            "smc_dir": signal.smc_dir,
            "signal_close_time": signal.close_time,
        }

    @staticmethod
    def validate_order_payload(payload: dict) -> None:
        side = str(payload.get("side", "")).upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"invalid side: {side!r}")

        entry = float(payload.get("entry", 0))
        tp = float(payload.get("tp", 0))
        sl = float(payload.get("sl", 0))
        amount = float(payload.get("usdt_amount", 0))
        if not all(math.isfinite(v) and v > 0 for v in (entry, tp, sl, amount)):
            raise ValueError("entry/tp/sl/usdt_amount must be finite and > 0")

        if side == "BUY" and not (sl < entry < tp):
            raise ValueError(
                f"BUY price ordering invalid: sl={sl} entry={entry} tp={tp}"
            )
        if side == "SELL" and not (tp < entry < sl):
            raise ValueError(
                f"SELL price ordering invalid: tp={tp} entry={entry} sl={sl}"
            )

        if payload.get("order_type") != "MARKET":
            raise ValueError("order_type must be MARKET")
        if not payload.get("signal_id"):
            raise ValueError("signal_id is empty")

    @staticmethod
    def _response_ok(status_code: int, text: str) -> bool:
        if not (200 <= status_code < 300):
            return False
        try:
            body = json.loads(text) if text else {}
        except Exception:
            return True
        if isinstance(body, dict):
            if "status" in body:
                return str(body.get("status")).lower() in {"success", "ok"}
            if "code" in body:
                return body.get("code") in {0, "0"}
        return True

    def _signed_trade_request(self, method: str, path: str, params: dict) -> dict:
        with self.request_guard.request_slot():
            return self._signed_trade_request_locked(method, path, params)

    def _signed_trade_request_locked(self, method: str, path: str, params: dict) -> dict:
        log.info("BINGX_TRADE_REQ method=%s path=%s symbol=%s", method, path, params.get("symbol"))
        params = dict(params)
        params.setdefault("recvWindow", 5000)
        params["timestamp"] = int(time.time() * 1000)

        # BingX signs the raw ASCII-sorted k=v string before URL encoding.
        query = "&".join(f"{k}={params[k]}" for k in sorted(params))
        signature = hmac.new(
            self.settings.bingx_api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers = {
            "X-BX-APIKEY": self.settings.bingx_api_key,
            "X-SOURCE-KEY": "BX-AI-SKILL",
        }
        base = f"{self.settings.bingx_base_url}{path}"
        method = method.upper()

        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            r = self.client.post(
                base,
                headers=headers,
                content=f"{query}&signature={signature}",
            )
        elif method == "GET":
            r = self.client.get(
                f"{base}?{query}&signature={signature}",
                headers=headers,
            )
        else:
            raise ValueError(f"unsupported BingX method: {method}")

        try:
            body = r.json()
        except Exception:
            body = {}

        log.info("BINGX_TRADE_HTTP method=%s path=%s status=%s", method, path, r.status_code)
        code = body.get("code") if isinstance(body, dict) else None
        if code not in (None, 0, "0"):
            msg = body.get("msg", "") if isinstance(body, dict) else ""
            raise self.request_guard.api_error("executor", code, str(msg), path, params)
        if r.status_code == 429:
            raise self.request_guard.api_error(
                "executor", "HTTP_429", "BingX HTTP rate limit", path, params
            )
        r.raise_for_status()
        if code not in (0, "0"):
            raise self.request_guard.api_error(
                "executor", "INVALID_RESPONSE", "missing success code", path, params
            )
        return body

    @staticmethod
    def _extract_order(payload: dict) -> dict:
        """Support both legacy data.order and current direct data response shapes."""
        data = payload.get("data") or {}
        if isinstance(data, dict) and isinstance(data.get("order"), dict):
            return data["order"]
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _floor_precision(value: float, precision: int) -> float:
        factor = 10 ** max(0, int(precision))
        return math.floor(value * factor + 1e-12) / factor

    def _contract_rules(self, symbol: str) -> dict:
        cached = self._contract_cache.get(symbol)
        if cached:
            return cached

        body = self._signed_trade_request(
            "GET",
            "/openApi/swap/v2/quote/contracts",
            {"symbol": symbol},
        )
        data = body.get("data") or []
        if isinstance(data, dict):
            data = data.get("contracts") or data.get("data") or [data]
        if not isinstance(data, list):
            data = [data]

        item = next(
            (
                row for row in data
                if isinstance(row, dict)
                and str(row.get("symbol", "")).upper() == symbol.upper()
            ),
            None,
        )
        if not item:
            raise RuntimeError(f"BingX contract info not found for {symbol}")

        rules = {
            "quantity_precision": int(item.get("quantityPrecision", 4)),
            "price_precision": int(item.get("pricePrecision", 2)),
            "min_qty": float(item.get("tradeMinQuantity") or 0),
            "min_usdt": float(item.get("tradeMinUSDT") or 0),
            "max_long_leverage": int(item.get("maxLongLeverage") or 0),
            "max_short_leverage": int(item.get("maxShortLeverage") or 0),
            "status": int(item.get("status", 1)),
            "api_state_open": str(item.get("apiStateOpen", "true")).lower(),
            "api_state_close": str(item.get("apiStateClose", "true")).lower(),
        }
        self._contract_cache[symbol] = rules
        return rules

    def _assert_hedge_mode(self) -> None:
        body = self._signed_trade_request(
            "GET", "/openApi/swap/v1/positionSide/dual", {}
        )
        data = body.get("data") or {}
        raw = data.get("dualSidePosition") if isinstance(data, dict) else None
        is_hedge = raw is True or str(raw).lower() == "true"
        if not is_hedge:
            raise RuntimeError(
                "BingX position mode must be Hedge Mode because orders use positionSide LONG/SHORT"
            )

    def _prepare_fixed_size(self, symbol: str, side: str, entry: float) -> dict:
        rules = self._contract_rules(symbol)

        if rules["status"] != 1 or rules["api_state_open"] == "false":
            raise RuntimeError(f"{symbol} is not open for API entries")

        max_lev = (
            rules["max_long_leverage"]
            if side.upper() == "BUY"
            else rules["max_short_leverage"]
        )
        if max_lev > 0 and self.settings.leverage > max_lev:
            raise RuntimeError(
                f"{symbol} max leverage is {max_lev}x, configured {self.settings.leverage}x"
            )

        notional = self.settings.order_margin_usdt * self.settings.leverage
        raw_qty = notional / entry
        qty = self._floor_precision(raw_qty, rules["quantity_precision"])
        if qty <= 0:
            raise ValueError("fixed sizing produced zero quantity")

        actual_notional = qty * entry
        if rules["min_qty"] > 0 and qty < rules["min_qty"]:
            raise ValueError(
                f"quantity {qty} is below BingX minimum {rules['min_qty']}"
            )
        if rules["min_usdt"] > 0 and actual_notional < rules["min_usdt"]:
            raise ValueError(
                f"notional {actual_notional:.8f} is below BingX minimum {rules['min_usdt']}"
            )

        return {
            **rules,
            "target_notional": notional,
            "actual_notional": actual_notional,
            "qty": qty,
        }

    def _place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "MARKET",
        price: float | None = None,
        stop_price: float | None = None,
        activation_price: float | None = None,
        price_rate: float | None = None,
        position_side: str | None = None,
        leverage: int | None = None,
        close_position: bool = False,
        client_order_id: str | None = None,
    ) -> dict:
        position_side = position_side or ("LONG" if side.upper() == "BUY" else "SHORT")
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "positionSide": position_side,
            "type": order_type.upper(),
            "quantity": f"{qty:.8f}".rstrip("0").rstrip("."),
        }
        # BingX leverage is configured through /trade/leverage, not the order payload.
        if close_position:
            params["closePosition"] = "true"
        if client_order_id:
            params["clientOrderId"] = client_order_id
        if price is not None:
            params["price"] = str(price)
        if stop_price is not None:
            params["stopPrice"] = str(stop_price)
        if activation_price is not None:
            params["activationPrice"] = str(activation_price)
        if price_rate is not None:
            params["priceRate"] = str(price_rate)
        return self._signed_trade_request(
            "POST", "/openApi/swap/v2/trade/order", params
        )

    def _positions(self, symbol: str) -> list[dict]:
        body = self._signed_trade_request(
            "GET",
            "/openApi/swap/v2/user/positions",
            {"symbol": symbol},
        )
        data = body.get("data") or []
        if isinstance(data, dict):
            data = data.get("positions") or data.get("data") or [data]
        return [x for x in data if isinstance(x, dict)]

    def _set_leverage(self, symbol: str, side: str, leverage: int) -> dict:
        return self._signed_trade_request(
            "POST",
            "/openApi/swap/v2/trade/leverage",
            {
                "symbol": symbol,
                "side": "LONG" if side.upper() == "BUY" else "SHORT",
                "leverage": int(leverage),
            },
        )

    def _order_detail(self, symbol: str, order_id: str) -> dict:
        return self._signed_trade_request(
            "GET",
            "/openApi/swap/v2/trade/order",
            {"symbol": symbol, "orderId": order_id},
        )

    def _execute_direct_bingx(self, signal: Signal) -> dict:
        entry, tp, sl = execution_prices(signal, self.settings)

        # FINAL14 sizing: 100 USDT notional at 50x.
        # TP/SL are locked strategy outputs and never participate in volume sizing.
        self._assert_hedge_mode()
        sizing = self._prepare_fixed_size(signal.symbol, signal.side, entry)
        qty = float(sizing["qty"])
        notional = float(sizing["actual_notional"])

        price_precision = int(sizing["price_precision"])
        tp = round(tp, price_precision)
        sl = round(sl, price_precision)

        payload = self.build_payload(signal, notional)
        payload["tp"] = tp
        payload["sl"] = sl
        self.validate_order_payload(payload)

        log.info(
            "FIXED_SIZE symbol=%s signal_id=%s margin_usdt=%.8f leverage=%s target_notional=%.8f actual_notional=%.8f qty=%s qty_precision=%s price_precision=%s",
            signal.symbol,
            signal.event_id,
            self.settings.order_margin_usdt,
            self.settings.leverage,
            sizing["target_notional"],
            sizing["actual_notional"],
            sizing["qty"],
            sizing["quantity_precision"],
            sizing["price_precision"],
        )

        self._set_leverage(
            signal.symbol, signal.side, int(self.settings.leverage)
        )

        # Deterministic ID protects against accidental duplicate MARKET entry.
        client_order_id = "sig" + hashlib.sha256(
            signal.event_id.encode("utf-8")
        ).hexdigest()[:28]

        entry_result = self._place_order(
            signal.symbol,
            signal.side,
            qty,
            client_order_id=client_order_id,
        )
        order = self._extract_order(entry_result)
        order_id = order.get("orderID") or order.get("orderId")
        if not order_id:
            raise RuntimeError("BingX entry order did not return orderId")

        executed_qty = 0.0
        avg_price = 0.0
        status = ""
        for _ in range(10):
            detail = self._order_detail(signal.symbol, str(order_id))
            order = self._extract_order(detail)
            executed_qty = float(order.get("executedQty") or 0)
            avg_price = float(order.get("avgPrice") or 0)
            status = str(order.get("status") or "")
            if executed_qty > 0 and avg_price > 0:
                break
            time.sleep(1.5)

        if executed_qty <= 0 or avg_price <= 0:
            raise RuntimeError(
                f"entry not confirmed filled: status={status} executed_qty={executed_qty} avg_price={avg_price}"
            )

        valid = (
            (signal.side == "BUY" and sl < avg_price < tp)
            or (signal.side == "SELL" and tp < avg_price < sl)
        )
        if not valid:
            close_side = "SELL" if signal.side == "BUY" else "BUY"
            close_result = self._place_order(
                signal.symbol,
                close_side,
                executed_qty,
                order_type="MARKET",
                position_side="LONG" if signal.side == "BUY" else "SHORT",
            )
            return {
                "ok": False,
                "closed_for_invalid_fill": True,
                "order_id": str(order_id),
                "avg_price": avg_price,
                "executed_qty": executed_qty,
                "close_result": close_result,
                "reason": "filled_price_outside_tp_sl",
            }

        opposite = "SELL" if signal.side == "BUY" else "BUY"
        entry_position_side = "LONG" if signal.side == "BUY" else "SHORT"
        exit_qty = self._floor_precision(
            executed_qty, int(sizing["quantity_precision"])
        )

        # FINAL14 exit contract: 100% hard TP + 100% hard SL.
        # No partial exit and no trailing stop.
        tp_result = self._place_order(
            signal.symbol,
            opposite,
            exit_qty,
            order_type="TAKE_PROFIT_MARKET",
            stop_price=tp,
            position_side=entry_position_side,
        )
        sl_result = self._place_order(
            signal.symbol,
            opposite,
            exit_qty,
            order_type="STOP_MARKET",
            stop_price=sl,
            position_side=entry_position_side,
            close_position=True,
        )

        return {
            "ok": True,
            "order_id": str(order_id),
            "avg_price": avg_price,
            "executed_qty": executed_qty,
            "tp": tp,
            "sl": sl,
            "exit_mode": "100pct_hard_tp_sl",
            "entry_result": entry_result,
            "tp_result": tp_result,
            "sl_result": sl_result,
        }

    def send_target(self, signal: Signal, target_name: str, url: str, usdt_amount: float) -> dict:
        # usdt_amount is intentionally ignored. Live size is fixed by
        # BINGX_ORDER_MARGIN_USDT * BINGX_LEVERAGE.
        if self.settings.dry_run:
            return {"target": target_name, "ok": False, "error": "dry_run_disabled_by_live_policy"}

        try:
            result = self._execute_direct_bingx(signal)
            result["target"] = target_name
            result["payload"] = self.build_payload(
                signal, self.settings.order_margin_usdt * self.settings.leverage
            )
            log.info(
                "BINGX_DIRECT_EXEC target=%s signal_id=%s ok=%s order_id=%s avg_price=%s qty=%s",
                target_name,
                signal.event_id,
                result.get("ok"),
                result.get("order_id"),
                result.get("avg_price"),
                result.get("executed_qty"),
            )
            return result
        except Exception as exc:
            accepted_order_id = str(getattr(exc, "accepted_order_id", "") or "")
            log.exception(
                "BINGX_DIRECT_EXEC_FAILED target=%s signal_id=%s accepted_order_id=%s",
                target_name, signal.event_id, accepted_order_id or None,
            )
            return {
                "target": target_name,
                "ok": False,
                "processed": bool(accepted_order_id),
                "entry_accepted": bool(accepted_order_id),
                "entry_filled": False,
                "stage": "post_entry_error" if accepted_order_id else "pre_entry_error",
                "order_id": accepted_order_id,
                "error": str(exc),
                "payload": self.build_payload(
                    signal,
                    self.settings.order_margin_usdt * self.settings.leverage,
                ),
            }
