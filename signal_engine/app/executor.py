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
from .strategy import Signal

log = logging.getLogger(__name__)


def _js_round_positive(x: float) -> int:
    return int(math.floor(x + 0.5))


def _round2_js(x: float) -> float:
    return math.floor(x * 100.0 + 0.5) / 100.0


def execution_prices(signal: Signal, settings: Settings) -> tuple[float, float, float]:
    """Mirror the current Gmail Apps Script execution transformation."""
    entry = signal.entry
    tp = signal.tp
    sl = signal.sl

    if settings.legacy_rounding:
        entry = float(_js_round_positive(entry))
        tp = float(_js_round_positive(tp))
        sl = float(_js_round_positive(sl))

    shift = settings.adjust_tp_sl_bps / 10000.0
    if signal.side == "BUY":
        tp *= 1.0 + shift
        sl *= 1.0 + shift
    else:
        tp *= 1.0 - shift
        sl *= 1.0 - shift

    return entry, _round2_js(tp), _round2_js(sl)


class Executor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(timeout=20.0)
        self._contract_cache: dict[str, dict] = {}

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

    @staticmethod
    def _fmt_discord_number(value) -> str:
        if value is None:
            return "-"
        try:
            n = float(value)
            if not math.isfinite(n):
                return str(value)
            return f"{n:.8f}".rstrip("0").rstrip(".")
        except Exception:
            return str(value)

    def build_execution_discord_message(self, signal: Signal, result: dict) -> str:
        """Compact trade notification matching the legacy Discord layout."""
        entry = self._fmt_discord_number(result.get("avg_price"))
        tp = self._fmt_discord_number(result.get("tp_actual", result.get("tp")))
        sl = self._fmt_discord_number(result.get("sl_actual", result.get("sl")))
        trailing = self._fmt_discord_number(
            result.get("trailing_activation_actual", result.get("trailing_activation"))
        )

        return (
            "✅ Đặt lệnh\n"
            f"{signal.symbol} {signal.side}\n\n"
            f"📊 Combo {signal.combo}\n"
            f"Entry: {entry}\n\n"
            f"TP : {tp}\n"
            f"SL : {sl}\n\n"
            f"Trailing : {trailing}"
        )

    def build_execution_error_message(self, signal: Signal, result: dict) -> str:
        """Short operator alert; detailed diagnostics stay in Render logs."""
        stage = str(result.get("stage") or "unknown")
        stage_labels = {
            "execution_blocked": "Hệ thống",
            "entry_fill_check": "Xác nhận Entry",
            "invalid_fill_emergency_close": "Giá khớp / đóng khẩn cấp",
            "stop_loss": "Cài SL",
            "protection_partial": "TP / Trailing",
            "one_shot_startup": "Khởi tạo",
            "one_shot_eth_startup": "Khởi tạo",
        }
        label = stage_labels.get(stage, stage)

        if result.get("emergency_close_attempted"):
            if result.get("emergency_close_ok"):
                status = "Vị thế đã được đóng khẩn cấp ✅"
            else:
                status = "Đóng khẩn cấp thất bại ❌"
        elif result.get("entry_filled"):
            status = "Entry đã khớp nhưng bảo vệ chưa hoàn tất ⚠️"
        else:
            status = "Không đặt được lệnh ❌"

        return (
            "⚠️ Lỗi đặt lệnh\n"
            f"{signal.symbol} {signal.side}\n\n"
            f"📊 Combo {signal.combo}\n"
            f"Bước: {label}\n"
            f"{status}"
        )

    def _post_discord(self, url: str, content: str, target: str) -> dict:
        if not url:
            return {"target": target, "ok": False, "skipped": True, "reason": "no_webhook"}

        payload = {"content": content}
        for attempt in range(1, 4):
            try:
                r = self.client.post(url, json=payload)
                if r.status_code in (200, 204):
                    log.info("Discord target=%s sent", target)
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
                log.warning(
                    "Discord target=%s failed status=%s body=%s",
                    target, r.status_code, r.text[:300],
                )
                time.sleep(1.0)
            except Exception as exc:
                log.warning(
                    "Discord target=%s attempt=%s failed: %s",
                    target, attempt, exc,
                )
                time.sleep(1.0)

        return {"target": target, "ok": False, "error": "discord_send_failed"}

    def send_execution_discord(self, signal: Signal, result: dict) -> dict:
        target = f"discord:{signal.symbol}"
        if not self.discord_allowed():
            return {"target": target, "ok": False, "skipped": True, "reason": "disabled"}
        return self._post_discord(
            self.discord_url(signal.symbol),
            self.build_execution_discord_message(signal, result),
            target,
        )

    def send_execution_error_discord(self, signal: Signal, result: dict) -> dict:
        target = "discord:error"
        if not self.settings.discord_enabled:
            return {"target": target, "ok": False, "skipped": True, "reason": "disabled"}
        return self._post_discord(
            self.settings.discord_webhook_error,
            self.build_execution_error_message(signal, result),
            target,
        )

    def send_execution_raw_discord(self, signal: Signal, result: dict) -> dict:
        """Send compact raw BingX execution responses without secrets/signatures."""
        target = f"discord:raw:{signal.symbol}"
        if not self.discord_allowed():
            return {"target": target, "ok": False, "skipped": True, "reason": "disabled"}

        raw = {
            "entry_result": result.get("entry_result"),
            "sl_result": result.get("sl_result"),
            "tp_result": result.get("tp_result"),
            "trailing_result": result.get("trailing_result"),
            "stage": result.get("stage"),
            "ok": result.get("ok"),
            "order_id": result.get("order_id"),
            "avg_price": result.get("avg_price"),
            "executed_qty": result.get("executed_qty"),
            "sl_ok": result.get("sl_ok"),
            "tp_ok": result.get("tp_ok"),
            "trailing_ok": result.get("trailing_ok"),
            "error": result.get("error"),
        }
        compact = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        content = (
            f"📡 **BINGX RAW RESPONSE | {signal.symbol} {signal.side}**\n"
            f"```json\n{compact[:1750]}\n```"
        )
        return self._post_discord(
            self.discord_url(signal.symbol),
            content,
            target,
        )

    def send_emergency_close_discord(self, symbol: str, result: dict) -> dict:
        target = f"discord:close:{symbol}"
        if not self.discord_allowed():
            return {"target": target, "ok": False, "skipped": True, "reason": "disabled"}

        close_order = result.get("close_order") or {}
        raw = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        status_text = "✅ CLOSED" if result.get("ok") else "❌ INCOMPLETE"
        close_order_id = close_order.get("orderId") or close_order.get("orderID") or "N/A"
        content = (
            f"🧯 **EMERGENCY CLOSE TEST | {symbol} LONG**\n"
            f"Status: **{status_text}**\n"
            f"Stage: `{result.get('stage')}`\n"
            f"Requested qty: `{result.get('requested_close_qty')}`\n"
            f"Remaining LONG: `{result.get('remaining_long_qty')}`\n"
            f"Close order ID: `{close_order_id}`\n"
            f"```json\n{raw[:1350]}\n```"
        )
        return self._post_discord(
            self.discord_url(symbol),
            content[:1950],
            target,
        )

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
            "source": "bingx-render-engine",
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
        elif method == "DELETE":
            r = self.client.delete(
                f"{base}?{query}&signature={signature}",
                headers=headers,
            )
        else:
            raise ValueError(f"unsupported BingX method: {method}")

        try:
            body = r.json()
        except Exception:
            body = {}

        if not (200 <= r.status_code < 300):
            snippet = (r.text or "")[:500].replace("\n", " ")
            raise RuntimeError(
                f"BingX HTTP {r.status_code} | endpoint={path} | body={snippet}"
            )

        code = body.get("code") if isinstance(body, dict) else None
        if code not in (None, 0, "0"):
            msg = body.get("msg", "") if isinstance(body, dict) else ""
            raise RuntimeError(
                f"BingX trade error {code}: {msg} | endpoint={path}"
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

    @staticmethod
    def _split_exit_quantities(full_qty: float, precision: int) -> tuple[float, float]:
        """Split a precision-aligned position with TP priority and no remainder.

        When quantity units are odd, TP receives the extra unit and trailing
        receives the smaller half. Example at precision=2: 0.03 -> TP 0.02,
        trailing 0.01.
        """
        factor = 10 ** max(0, int(precision))
        total_units = int(round(float(full_qty) * factor))
        trailing_units = total_units // 2
        tp_units = total_units - trailing_units
        return tp_units / factor, trailing_units / factor

    @staticmethod
    def _trailing_qty_is_valid(
        trailing_qty: float,
        price: float,
        min_qty: float,
        min_usdt: float,
    ) -> bool:
        if trailing_qty <= 0:
            return False
        if min_qty > 0 and trailing_qty < min_qty:
            return False
        if min_usdt > 0 and trailing_qty * price < min_usdt:
            return False
        return True

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

        tp_qty, trailing_qty = self._split_exit_quantities(
            qty, rules["quantity_precision"]
        )
        if not self._trailing_qty_is_valid(
            trailing_qty,
            entry,
            rules["min_qty"],
            rules["min_usdt"],
        ):
            # TP has priority. If the trailing leg cannot meet BingX quantity/
            # notional constraints, route the entire closeable position to TP
            # instead of rejecting or force-closing the entry.
            tp_qty = qty
            trailing_qty = 0.0

        if tp_qty <= 0:
            raise ValueError("TP exit quantity is zero")
        if rules["min_qty"] > 0 and tp_qty < rules["min_qty"]:
            raise ValueError(
                f"TP exit quantity {tp_qty} is below BingX minimum {rules['min_qty']}"
            )
        if rules["min_usdt"] > 0 and tp_qty * entry < rules["min_usdt"]:
            raise ValueError(
                f"TP exit notional {tp_qty * entry:.8f} is below BingX minimum {rules['min_usdt']}"
            )

        return {
            **rules,
            "target_notional": notional,
            "actual_notional": actual_notional,
            "qty": qty,
            # Backward-compatible alias used by the existing preflight response.
            "half_qty": tp_qty,
            "tp_qty": tp_qty,
            "trailing_qty": trailing_qty,
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

    def _cancel_all_open_orders(self, symbol: str) -> dict:
        return self._signed_trade_request(
            "DELETE",
            "/openApi/swap/v2/trade/allOpenOrders",
            {"symbol": symbol},
        )

    def emergency_close_position(self, symbol: str, position_side: str) -> dict:
        """Cancel open protection orders, market-close one hedge leg, and verify it is flat."""
        symbol = symbol.upper()
        position_side = position_side.upper()
        if position_side not in {"LONG", "SHORT"}:
            raise ValueError(f"invalid position_side: {position_side}")

        before = self._positions(symbol)
        pos = next(
            (
                p for p in before
                if str(p.get("positionSide", "")).upper() == position_side
                and abs(float(p.get("positionAmt") or 0)) > 0
            ),
            None,
        )
        if not pos:
            return {
                "ok": True,
                "stage": "no_position",
                "symbol": symbol,
                "position_side": position_side,
                "positions_before": before,
                "remaining_qty": 0.0,
            }

        rules = self._contract_rules(symbol)
        qty_precision = int(rules["quantity_precision"])
        available = abs(float(pos.get("availableAmt") or 0))
        position_amt = abs(float(pos.get("positionAmt") or 0))
        qty = self._floor_precision(
            available if available > 0 else position_amt,
            qty_precision,
        )
        if qty <= 0:
            raise RuntimeError(
                f"{symbol} {position_side} exists but closeable quantity is zero "
                f"(positionAmt={position_amt}, availableAmt={available})"
            )

        cancel_result = self._cancel_all_open_orders(symbol)
        close_side = "SELL" if position_side == "LONG" else "BUY"
        client_order_id = "emg" + hashlib.sha256(
            f"{symbol}|{position_side}|{pos.get('positionId')}|{qty}".encode("utf-8")
        ).hexdigest()[:28]
        close_result = self._place_order(
            symbol,
            close_side,
            qty,
            order_type="MARKET",
            position_side=position_side,
            client_order_id=client_order_id,
        )
        close_order = self._extract_order(close_result)
        close_order_id = close_order.get("orderID") or close_order.get("orderId")
        confirmed_close = (
            self._confirmed_order(symbol, close_result)
            if close_order_id else close_order
        )

        time.sleep(0.8)
        after = self._positions(symbol)
        remaining = sum(
            abs(float(p.get("positionAmt") or 0))
            for p in after
            if str(p.get("positionSide", "")).upper() == position_side
        )
        remaining = self._floor_precision(remaining, qty_precision)

        return {
            "ok": remaining <= 0,
            "stage": "emergency_close_verified" if remaining <= 0 else "emergency_close_incomplete",
            "symbol": symbol,
            "position_side": position_side,
            "position_id": pos.get("positionId"),
            "requested_close_qty": qty,
            "cancel_open_orders_result": cancel_result,
            "close_result": close_result,
            "close_order": confirmed_close,
            "positions_before": before,
            "positions_after": after,
            "remaining_qty": remaining,
            "remaining_long_qty": remaining if position_side == "LONG" else None,
            "remaining_short_qty": remaining if position_side == "SHORT" else None,
        }

    def emergency_close_long(self, symbol: str) -> dict:
        return self.emergency_close_position(symbol, "LONG")

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

    def _confirmed_order(self, symbol: str, create_result: dict) -> dict:
        """Return BingX's latest order snapshot when an orderId is available."""
        order = self._extract_order(create_result)
        order_id = order.get("orderID") or order.get("orderId")
        if not order_id:
            return order
        try:
            detail = self._order_detail(symbol, str(order_id))
            confirmed = self._extract_order(detail)
            return confirmed or order
        except Exception as exc:
            log.warning(
                "BINGX_ORDER_CONFIRM_FAILED symbol=%s order_id=%s error=%s",
                symbol, order_id, exc,
            )
            return order

    def _confirm_protection_order(
        self,
        symbol: str,
        label: str,
        create_result: dict,
    ) -> dict:
        """Require a real BingX orderId before protection is marked successful.

        This keeps the legacy separate-order flow, but prevents a TP/SL/trailing
        request from being reported as OK when BingX did not actually create an
        order. A failed follow-up GET is not retried as a new order because that
        could duplicate protection after an ambiguous network response.
        """
        created = self._extract_order(create_result)
        order_id = created.get("orderID") or created.get("orderId")
        if not order_id:
            raise RuntimeError(f"{label} response did not return orderId")

        confirmed = self._confirmed_order(symbol, create_result)
        status = str(confirmed.get("status") or created.get("status") or "").upper()
        if status in {"REJECTED", "CANCELED", "CANCELLED", "EXPIRED"}:
            raise RuntimeError(
                f"{label} order {order_id} is not active; status={status}"
            )

        log.info(
            "BINGX_PROTECTION_CONFIRMED symbol=%s label=%s order_id=%s status=%s",
            symbol,
            label,
            order_id,
            status or "UNKNOWN",
        )
        return confirmed or created

    def _execute_direct_bingx(self, signal: Signal) -> dict:
        entry, tp, sl = execution_prices(signal, self.settings)

        # Fixed sizing: 1 USDT margin per order at 100x.
        # TP/SL are strategy outputs and never participate in volume sizing.
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

        base_result = {
            "processed": True,
            "entry_accepted": True,
            "entry_filled": False,
            "protection_mode": "legacy_separate_orders",
            "order_id": str(order_id),
            "tp": tp,
            "sl": sl,
            "entry_result": entry_result,
            "tp_ok": None,
            "sl_ok": None,
            "trailing_ok": None,
        }

        executed_qty = 0.0
        avg_price = 0.0
        status = ""
        try:
            for _ in range(10):
                detail = self._order_detail(signal.symbol, str(order_id))
                order = self._extract_order(detail)
                executed_qty = float(order.get("executedQty") or 0)
                avg_price = float(order.get("avgPrice") or 0)
                status = str(order.get("status") or "")
                if executed_qty > 0 and avg_price > 0:
                    break
                time.sleep(1.5)
        except Exception as exc:
            return {
                **base_result,
                "ok": False,
                "stage": "entry_fill_check",
                "status": status,
                "executed_qty": executed_qty,
                "avg_price": avg_price,
                "error": str(exc),
            }

        if executed_qty <= 0 or avg_price <= 0:
            return {
                **base_result,
                "ok": False,
                "stage": "entry_fill_check",
                "status": status,
                "executed_qty": executed_qty,
                "avg_price": avg_price,
                "error": (
                    "entry accepted but fill not confirmed "
                    f"status={status} executed_qty={executed_qty} avg_price={avg_price}"
                ),
            }

        base_result.update({
            "entry_filled": True,
            "avg_price": avg_price,
            "executed_qty": executed_qty,
            "status": status,
        })

        valid = (
            (signal.side == "BUY" and sl < avg_price < tp)
            or (signal.side == "SELL" and tp < avg_price < sl)
        )
        if not valid:
            close_side = "SELL" if signal.side == "BUY" else "BUY"
            try:
                close_result = self._place_order(
                    signal.symbol,
                    close_side,
                    executed_qty,
                    order_type="MARKET",
                    position_side="LONG" if signal.side == "BUY" else "SHORT",
                )
                close_ok = True
                close_error = None
            except Exception as exc:
                close_result = None
                close_ok = False
                close_error = str(exc)

            return {
                **base_result,
                "ok": False,
                "stage": "invalid_fill_emergency_close",
                "closed_for_invalid_fill": close_ok,
                "emergency_close_attempted": True,
                "emergency_close_ok": close_ok,
                "close_result": close_result,
                "reason": "filled_price_outside_tp_sl",
                "error": close_error or "filled price outside TP/SL; position auto-closed",
            }

        opposite = "SELL" if signal.side == "BUY" else "BUY"
        entry_position_side = "LONG" if signal.side == "BUY" else "SHORT"
        qty_precision = int(sizing["quantity_precision"])
        full_qty = self._floor_precision(executed_qty, qty_precision)
        tp_qty, trailing_qty = self._split_exit_quantities(full_qty, qty_precision)

        # TP priority for unexpected partial fills. If the smaller trailing leg
        # cannot satisfy BingX constraints, keep the position open and place TP
        # for 100% of the filled quantity; SL still protects 100% as well.
        if not self._trailing_qty_is_valid(
            trailing_qty,
            avg_price,
            float(sizing["min_qty"]),
            float(sizing["min_usdt"]),
        ):
            tp_qty = full_qty
            trailing_qty = 0.0

        base_result.update({
            "full_qty": full_qty,
            "tp_qty": tp_qty,
            "trailing_qty": trailing_qty,
            "exit_qty_total": tp_qty + trailing_qty,
        })
        log.info(
            "EXIT_SPLIT symbol=%s signal_id=%s full_qty=%s tp_qty=%s trailing_qty=%s covered_qty=%s mode=%s",
            signal.symbol,
            signal.event_id,
            full_qty,
            tp_qty,
            trailing_qty,
            tp_qty + trailing_qty,
            "tp_only" if trailing_qty <= 0 else "tp_priority_split",
        )

        # Legacy-proven protection format:
        # opposite side + ORIGINAL positionSide + explicit quantity.
        # Do not send closePosition=true here; the previous stable server did
        # not use it for STOP_MARKET and some BingX account/mode combinations
        # reject closePosition together with this conditional-order shape.
        #
        # SL is placed first because it is mandatory protection. If it fails,
        # immediately try to flatten the filled position before creating TP/trailing.
        try:
            sl_result = self._place_order(
                signal.symbol,
                opposite,
                full_qty,
                order_type="STOP_MARKET",
                stop_price=sl,
                position_side=entry_position_side,
            )
            sl_order = self._confirm_protection_order(
                signal.symbol, "SL", sl_result
            )
            sl_actual = float(sl_order.get("stopPrice") or sl)
            base_result.update({
                "sl_ok": True,
                "sl_result": sl_result,
                "sl_actual": sl_actual,
            })
        except Exception as exc:
            emergency_close = None
            emergency_close_ok = False
            emergency_close_error = None
            try:
                emergency_close = self.emergency_close_position(
                    signal.symbol,
                    entry_position_side,
                )
                emergency_close_ok = bool(emergency_close.get("ok"))
                if not emergency_close_ok:
                    emergency_close_error = (
                        f"remaining position after emergency close: "
                        f"{emergency_close.get('remaining_qty')}"
                    )
            except Exception as close_exc:
                emergency_close_error = str(close_exc)

            return {
                **base_result,
                "ok": False,
                "stage": "stop_loss",
                "sl_ok": False,
                "error": str(exc),
                "emergency_close_attempted": True,
                "emergency_close_ok": emergency_close_ok,
                "emergency_close_result": emergency_close,
                "emergency_close_error": emergency_close_error,
            }

        protection_errors: list[str] = []

        try:
            tp_result = self._place_order(
                signal.symbol,
                opposite,
                tp_qty,
                order_type="TAKE_PROFIT_MARKET",
                stop_price=tp,
                position_side=entry_position_side,
            )
            tp_order = self._confirm_protection_order(
                signal.symbol, "TP", tp_result
            )
            tp_actual = float(tp_order.get("stopPrice") or tp)
            base_result.update({
                "tp_ok": True,
                "tp_result": tp_result,
                "tp_actual": tp_actual,
            })
        except Exception as exc:
            base_result["tp_ok"] = False
            protection_errors.append(f"TP: {exc}")

        risk = abs(avg_price - sl)
        activation = round(
            avg_price + risk * 0.5 if signal.side == "BUY"
            else avg_price - risk * 0.5,
            price_precision,
        )
        base_result["trailing_activation"] = activation

        if trailing_qty <= 0:
            base_result.update({
                "trailing_ok": True,
                "trailing_skipped": True,
                "trailing_skip_reason": "quantity_too_small_tp_priority",
                "trailing_result": None,
                "trailing_activation_actual": None,
            })
        else:
            try:
                trailing_result = self._place_order(
                    signal.symbol,
                    opposite,
                    trailing_qty,
                    order_type="TRAILING_STOP_MARKET",
                    activation_price=activation,
                    price_rate=0.005,
                    position_side=entry_position_side,
                )
                trailing_order = self._confirm_protection_order(
                    signal.symbol, "TRAILING", trailing_result
                )
                trailing_actual = float(
                    trailing_order.get("activationPrice")
                    or trailing_order.get("activatePrice")
                    or activation
                )
                base_result.update({
                    "trailing_ok": True,
                    "trailing_skipped": False,
                    "trailing_result": trailing_result,
                    "trailing_activation_actual": trailing_actual,
                })
            except Exception as exc:
                base_result["trailing_ok"] = False
                protection_errors.append(f"Trailing: {exc}")

        all_ok = bool(
            base_result.get("sl_ok")
            and base_result.get("tp_ok")
            and base_result.get("trailing_ok")
        )
        return {
            **base_result,
            "ok": all_ok,
            "stage": "complete" if all_ok else "protection_partial",
            "error": "; ".join(protection_errors) if protection_errors else None,
        }

    def send_target(self, signal: Signal, target_name: str, url: str, usdt_amount: float) -> dict:
        # usdt_amount is intentionally ignored. Live size is fixed by
        # BINGX_ORDER_MARGIN_USDT * BINGX_LEVERAGE.
        if self.settings.dry_run:
            return {
                "target": target_name,
                "ok": False,
                "processed": False,
                "stage": "policy",
                "error": "dry_run_disabled_by_live_policy",
            }

        try:
            result = self._execute_direct_bingx(signal)
            result["target"] = target_name
            result["payload"] = self.build_payload(
                signal, self.settings.order_margin_usdt * self.settings.leverage
            )
            log.info(
                "BINGX_DIRECT_EXEC target=%s signal_id=%s ok=%s processed=%s order_id=%s avg_price=%s qty=%s stage=%s",
                target_name,
                signal.event_id,
                result.get("ok"),
                result.get("processed"),
                result.get("order_id"),
                result.get("avg_price"),
                result.get("executed_qty"),
                result.get("stage"),
            )
            return result
        except Exception as exc:
            # Do not emit ERROR here: main sends one structured message to the
            # dedicated error webhook, avoiding duplicate Discord alerts from
            # the global ERROR log handler.
            log.warning(
                "BINGX_DIRECT_EXEC_FAILED target=%s signal_id=%s error=%s",
                target_name, signal.event_id, exc,
                exc_info=True,
            )
            return {
                "target": target_name,
                "ok": False,
                "processed": False,
                "entry_accepted": False,
                "entry_filled": False,
                "stage": "pre_entry",
                "error": str(exc),
                "payload": self.build_payload(
                    signal, self.settings.order_margin_usdt * self.settings.leverage
                ),
            }
