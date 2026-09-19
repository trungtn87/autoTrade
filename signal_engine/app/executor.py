from __future__ import annotations

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
        out = []
        if self.settings.webhook_1:
            out.append(("account_1", self.settings.webhook_1, self.settings.webhook_1_usdt))
        if self.settings.webhook_2:
            out.append(("account_2", self.settings.webhook_2, self.settings.webhook_2_usdt))
        return out

    def discord_url(self, symbol: str) -> str:
        symbol = symbol.upper()
        if symbol == "BTC-USDT" and self.settings.discord_webhook_btc:
            return self.settings.discord_webhook_btc
        if symbol == "ETH-USDT" and self.settings.discord_webhook_eth:
            return self.settings.discord_webhook_eth
        return self.settings.discord_webhook_default

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

    def send_target(self, signal: Signal, target_name: str, url: str, usdt_amount: float) -> dict:
        payload = self.build_payload(signal, usdt_amount)
        if self.settings.dry_run:
            log.warning("DRY_RUN %s: %s", target_name, json.dumps(payload, ensure_ascii=False))
            return {"target": target_name, "ok": True, "dry_run": True, "payload": payload}

        try:
            r = self.client.post(url, json=payload)
            ok = self._response_ok(r.status_code, r.text)
            result = {
                "target": target_name,
                "status_code": r.status_code,
                "ok": ok,
                "body": r.text[:1000],
                "payload": payload,
            }
            log.info("Webhook %s -> %s ok=%s body=%s", target_name, r.status_code, ok, r.text[:300])
            return result
        except Exception as exc:
            log.exception("Webhook %s failed", target_name)
            return {"target": target_name, "ok": False, "error": str(exc), "payload": payload}
