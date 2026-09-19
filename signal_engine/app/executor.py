from __future__ import annotations

import json
import logging
import math

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
