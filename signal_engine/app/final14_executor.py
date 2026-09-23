from __future__ import annotations

import hashlib
import json
import logging
import time

from .executor import Executor
from .final14_config import case_name, get_case
from .strategy import Signal

log = logging.getLogger(__name__)

FINAL14_NOTIONAL_USDT = 100.0
FINAL14_EXECUTION_LEVERAGE = 50


class Final14Executor(Executor):
    """Locked FINAL14 live execution.

    - 100 USDT notional per accepted signal.
    - 50x production leverage.
    - Hedge mode + SEPARATE_ISOLATED so each symbol+combo can own an
      independent position.
    - 100% hard TP + 100% hard SL attached to the MARKET entry.
    - No trailing and no partial exit.
    """

    def __init__(self, settings):
        super().__init__(settings)
        self._hedge_mode_verified = False
        self._separate_isolated_verified: set[str] = set()

    def _assert_hedge_mode(self) -> None:
        if self._hedge_mode_verified:
            return
        super()._assert_hedge_mode()
        self._hedge_mode_verified = True

    def _margin_type(self, symbol: str) -> str:
        body = self._signed_trade_request(
            "GET",
            "/openApi/swap/v2/trade/marginType",
            {"symbol": symbol},
        )
        data = body.get("data") or {}
        if isinstance(data, dict):
            return str(data.get("marginType") or "").upper()
        return ""

    def _assert_separate_isolated(self, symbol: str) -> None:
        symbol = symbol.upper()
        if symbol in self._separate_isolated_verified:
            return
        margin_type = self._margin_type(symbol)
        if margin_type != "SEPARATE_ISOLATED":
            raise RuntimeError(
                f"{symbol} marginType={margin_type or 'UNKNOWN'}; "
                "FINAL14 requires SEPARATE_ISOLATED for independent combo positions"
            )
        self._separate_isolated_verified.add(symbol)

    def _prepare_final14_size(self, signal: Signal) -> dict:
        rules = self._contract_rules(signal.symbol)
        if rules["status"] != 1 or rules["api_state_open"] == "false":
            raise RuntimeError(f"{signal.symbol} is not open for API entries")

        max_lev = (
            rules["max_long_leverage"]
            if signal.side.upper() == "BUY"
            else rules["max_short_leverage"]
        )
        if max_lev > 0 and FINAL14_EXECUTION_LEVERAGE > max_lev:
            raise RuntimeError(
                f"{signal.symbol} max leverage is {max_lev}x, "
                f"FINAL14 requires {FINAL14_EXECUTION_LEVERAGE}x"
            )

        raw_qty = FINAL14_NOTIONAL_USDT / float(signal.entry)
        qty = self._floor_precision(raw_qty, rules["quantity_precision"])
        if qty <= 0:
            raise ValueError("FINAL14 sizing produced zero quantity")

        actual_notional = qty * float(signal.entry)
        if rules["min_qty"] > 0 and qty < rules["min_qty"]:
            raise ValueError(
                f"quantity {qty} below BingX minimum {rules['min_qty']}"
            )
        if rules["min_usdt"] > 0 and actual_notional < rules["min_usdt"]:
            raise ValueError(
                f"notional {actual_notional:.8f} below BingX minimum {rules['min_usdt']}"
            )

        return {
            **rules,
            "qty": qty,
            "target_notional": FINAL14_NOTIONAL_USDT,
            "actual_notional": actual_notional,
        }

    @staticmethod
    def _position_ids(rows: list[dict]) -> set[str]:
        out = set()
        for row in rows:
            try:
                amt = abs(float(row.get("positionAmt") or 0))
            except Exception:
                amt = 0.0
            pid = str(row.get("positionId") or "")
            if pid and amt > 0:
                out.add(pid)
        return out

    def _resolve_new_position_id(
        self,
        symbol: str,
        side: str,
        before_ids: set[str],
        avg_price: float,
        executed_qty: float,
    ) -> str | None:
        wanted_side = "LONG" if side.upper() == "BUY" else "SHORT"
        for _ in range(12):
            rows = self._positions(symbol)
            candidates = []
            for row in rows:
                pid = str(row.get("positionId") or "")
                if not pid or pid in before_ids:
                    continue
                if str(row.get("positionSide") or "").upper() != wanted_side:
                    continue
                try:
                    amt = abs(float(row.get("positionAmt") or 0))
                    px = float(row.get("avgPrice") or 0)
                except Exception:
                    continue
                if amt <= 0:
                    continue
                qty_err = abs(amt - executed_qty)
                px_err = abs(px - avg_price) / avg_price if avg_price > 0 else 999
                candidates.append((qty_err, px_err, pid))
            if candidates:
                candidates.sort()
                return candidates[0][2]
            time.sleep(0.5)
        return None

    def build_payload(self, signal: Signal, usdt_amount: float) -> dict:
        cfg = get_case(signal.symbol, signal.combo)
        return {
            "signal_id": signal.event_id,
            "combo": case_name(signal.combo),
            "side": signal.side,
            "entry": float(signal.entry),
            "tp": float(signal.tp),
            "sl": float(signal.sl),
            "rr": float(cfg["rr"]) if cfg else None,
            "symbol": signal.symbol,
            "timeframe": signal.timeframe,
            "order_type": "MARKET",
            "usdt_amount": usdt_amount,
            "source": "bingx-final14-hardtp",
            "smc_dir": signal.smc_dir,
            "signal_close_time": signal.close_time,
            "exit_mode": "100pct_hard_tp_sl",
        }

    def _execute_direct_bingx(self, signal: Signal) -> dict:
        cfg = get_case(signal.symbol, signal.combo)
        if not cfg:
            raise RuntimeError(
                f"FINAL14 disabled case: {signal.symbol} {case_name(signal.combo)}"
            )

        self._assert_hedge_mode()
        self._assert_separate_isolated(signal.symbol)
        sizing = self._prepare_final14_size(signal)
        qty = float(sizing["qty"])
        price_precision = int(sizing["price_precision"])

        # Exact locked strategy levels are based on the confirmed candle close.
        # Only exchange price precision is applied.
        tp = round(float(signal.tp), price_precision)
        sl = round(float(signal.sl), price_precision)
        entry = float(signal.entry)
        if signal.side == "BUY" and not (sl < entry < tp):
            raise ValueError("FINAL14 BUY TP/SL ordering invalid")
        if signal.side == "SELL" and not (tp < entry < sl):
            raise ValueError("FINAL14 SELL TP/SL ordering invalid")

        self._set_leverage(
            signal.symbol,
            signal.side,
            FINAL14_EXECUTION_LEVERAGE,
        )

        # Snapshot position IDs only when a signal is actually being executed.
        # This lets us resolve the independent SEPARATE_ISOLATED position later.
        before_ids = self._position_ids(self._positions(signal.symbol))

        client_order_id = "f14" + hashlib.sha256(
            signal.event_id.encode("utf-8")
        ).hexdigest()[:28]

        params = {
            "symbol": signal.symbol,
            "side": signal.side.upper(),
            "positionSide": "LONG" if signal.side == "BUY" else "SHORT",
            "type": "MARKET",
            "quantity": f"{qty:.8f}".rstrip("0").rstrip("."),
            "clientOrderId": client_order_id,
            "stopLoss": json.dumps(
                {
                    "type": "STOP_MARKET",
                    "stopPrice": sl,
                    "workingType": "CONTRACT_PRICE",
                    "stopGuaranteed": False,
                },
                separators=(",", ":"),
            ),
            "takeProfit": json.dumps(
                {
                    "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": tp,
                    "workingType": "CONTRACT_PRICE",
                    "stopGuaranteed": False,
                },
                separators=(",", ":"),
            ),
        }

        entry_result = self._signed_trade_request(
            "POST",
            "/openApi/swap/v2/trade/order",
            params,
        )
        order = self._extract_order(entry_result)
        order_id = order.get("orderID") or order.get("orderId")
        if not order_id:
            raise RuntimeError("FINAL14 entry did not return orderId")

        try:
            executed_qty = 0.0
            avg_price = 0.0
            status = ""
            order_detail = {}
            for _ in range(10):
                raw = self._order_detail(signal.symbol, str(order_id))
                order_detail = self._extract_order(raw)
                executed_qty = float(order_detail.get("executedQty") or 0)
                avg_price = float(order_detail.get("avgPrice") or 0)
                status = str(order_detail.get("status") or "")
                if executed_qty > 0 and avg_price > 0:
                    break
                time.sleep(1.0)

            if executed_qty <= 0 or avg_price <= 0:
                return {
                    "processed": True,
                    "entry_accepted": True,
                    "entry_filled": False,
                    "ok": False,
                    "stage": "entry_fill_check",
                    "order_id": str(order_id),
                    "position_id": "",
                    "status": status,
                    "tp": tp,
                    "sl": sl,
                    "rr": float(cfg["rr"]),
                    "error": "entry accepted but fill not confirmed",
                }

            position_id = (
                str(order_detail.get("positionId") or order.get("positionId") or "")
                or self._resolve_new_position_id(
                    signal.symbol,
                    signal.side,
                    before_ids,
                    avg_price,
                    executed_qty,
                )
                or ""
            )

            result = {
                "processed": True,
                "entry_accepted": True,
                "entry_filled": True,
                "ok": True,
                "stage": "complete" if position_id else "complete_position_id_pending",
                "order_id": str(order_id),
                "position_id": position_id,
                "avg_price": avg_price,
                "executed_qty": executed_qty,
                "target_notional": float(sizing["target_notional"]),
                "actual_notional": float(sizing["actual_notional"]),
                "execution_leverage": FINAL14_EXECUTION_LEVERAGE,
                "tp": tp,
                "sl": sl,
                "rr": float(cfg["rr"]),
                "tp_pct": float(cfg["tp_pct"]),
                "sl_pct": float(cfg["sl_pct"]),
                "protection_mode": "attached_hard_tp_sl",
                "working_type": "CONTRACT_PRICE",
                "entry_result": entry_result,
                "error": None,
            }
            log.info(
                "FINAL14_ENTRY symbol=%s combo=%s side=%s order_id=%s position_id=%s "
                "signal_entry=%s avg_fill=%s qty=%s tp=%s sl=%s rr=%s",
                signal.symbol,
                case_name(signal.combo),
                signal.side,
                order_id,
                position_id or None,
                entry,
                avg_price,
                executed_qty,
                tp,
                sl,
                cfg["rr"],
            )
            return result
        except Exception as exc:
            # BingX already accepted the entry. Preserve this fact so the
            # caller marks the event processed and never creates a duplicate.
            exc.accepted_order_id = str(order_id)
            raise
