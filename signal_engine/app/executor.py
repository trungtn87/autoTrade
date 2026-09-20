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
        params["timestamp"] = str(int(time.time() * 1000))
        query = "&".join(f"{k}={params[k]}" for k in sorted(params))
        signature = hmac.new(
            self.settings.bingx_api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        headers = {"X-BX-APIKEY": self.settings.bingx_api_key}
        url = f"{self.settings.bingx_base_url}{path}?{query}&signature={signature}"
        if method.upper() == "POST":
            r = self.client.post(url, headers=headers)
        else:
            r = self.client.get(url, headers=headers)
        r.raise_for_status()
        body = r.json()
        code = body.get("code")
        if code not in (None, 0, "0"):
            raise RuntimeError(f"BingX trade error {code}: {body.get('msg', '')}")
        return body

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

        # Fixed sizing: 1 USDT margin per order at configured leverage.
        # TP/SL are strategy outputs and do not participate in volume sizing.
        notional = self.settings.order_margin_usdt * self.settings.leverage
        if notional <= 0:
            raise ValueError("fixed order notional must be positive")

        qty = round(notional / entry, 4)
        if qty <= 0:
            raise ValueError("calculated quantity is not positive")

        payload = self.build_payload(signal, notional)
        self.validate_order_payload(payload)

        log.info(
            "FIXED_SIZE symbol=%s signal_id=%s margin_usdt=%.8f leverage=%s notional=%.8f qty=%s",
            signal.symbol,
            signal.event_id,
            self.settings.order_margin_usdt,
            self.settings.leverage,
            notional,
            qty,
        )

        self._set_leverage(
            signal.symbol, signal.side, int(self.settings.leverage)
        )
        entry_result = self._place_order(
            signal.symbol,
            signal.side,
            qty,
        )
        order = ((entry_result.get("data") or {}).get("order") or {})
        order_id = order.get("orderId")
        if not order_id:
            raise RuntimeError("BingX entry order did not return orderId")

        executed_qty = 0.0
        avg_price = 0.0
        status = ""
        for _ in range(10):
            detail = self._order_detail(signal.symbol, str(order_id))
            order = ((detail.get("data") or {}).get("order") or {})
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
        # Preserve the previous live-account behavior:
        # TP closes 50%, SL protects the full filled quantity.
        tp_result = self._place_order(
            signal.symbol,
            opposite,
            round(executed_qty * 0.5, 4),
            order_type="TAKE_PROFIT_MARKET",
            stop_price=tp,
            position_side=entry_position_side,
        )
        sl_result = self._place_order(
            signal.symbol,
            opposite,
            executed_qty,
            order_type="STOP_MARKET",
            stop_price=sl,
            position_side=entry_position_side,
        )

        risk = abs(avg_price - sl)
        activation = round(
            avg_price + risk * 0.5 if signal.side == "BUY"
            else avg_price - risk * 0.5,
            2,
        )
        trailing_result = self._place_order(
            signal.symbol,
            opposite,
            round(executed_qty * 0.5, 4),
            order_type="TRAILING_STOP_MARKET",
            activation_price=activation,
            price_rate=0.005,
            position_side=entry_position_side,
        )

        return {
            "ok": True,
            "order_id": str(order_id),
            "avg_price": avg_price,
            "executed_qty": executed_qty,
            "tp": tp,
            "sl": sl,
            "trailing_activation": activation,
            "entry_result": entry_result,
            "tp_result": tp_result,
            "sl_result": sl_result,
            "trailing_result": trailing_result,
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
            log.exception(
                "BINGX_DIRECT_EXEC_FAILED target=%s signal_id=%s",
                target_name, signal.event_id,
            )
            return {
                "target": target_name,
                "ok": False,
                "error": str(exc),
                "payload": self.build_payload(signal, 1.0),
            }
