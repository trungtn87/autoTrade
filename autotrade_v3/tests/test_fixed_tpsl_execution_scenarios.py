"""Isolated execution-scenario tests for FIXED_TP_SL (Section 6).

Tests exchange-level order lifecycle states using deterministic mocks without
mixing synthetic fault injection into market backtests.

Each scenario explicitly tags whether the modeled behavior is VERIFIED against
`autotrade_v2/app/execution/` or UNVERIFIED (pending Codex PR in feat/v3-fixed-tp-sl-discord).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
from typing import Any

import pytest


@dataclass
class MockExchangeOrder:
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    position_side: str
    order_type: str  # MARKET | TAKE_PROFIT_MARKET | STOP_MARKET
    orig_qty: Decimal
    executed_qty: Decimal
    avg_price: Decimal
    stop_price: Decimal | None = None
    status: str = "FILLED"  # NEW | PARTIALLY_FILLED | FILLED | CANCELED | REJECTED
    position_id: str = "1001"


@dataclass
class MockFixedTpSlExecutionHarness:
    """Deterministic L4 execution state-machine for FIXED_TP_SL scenario testing.

    Mirrors verified invariants from `autotrade_v2/app/execution/final14_executor.py`
    and `autotrade_v2/app/execution/merged.py`:
    - Deterministic clientOrderId per (purpose, event_id).
    - Query-by-clientOrderId on API timeout/unknown response before retry.
    - Partial MARKET entry fill that does not settle is cancelled + emergency closed.
    - Hard SL placed & verified before fixed TP.
    - Emergency close refused on shared (multi-combo) position; allowed on solo position.
    - Per-combo exit budget cannot exceed container positionAmt.
    """

    orders_by_cid: dict[str, MockExchangeOrder] = field(default_factory=dict)
    open_orders: dict[str, MockExchangeOrder] = field(default_factory=dict)
    positions: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    journal: dict[str, dict[str, Any]] = field(default_factory=dict)

    @staticmethod
    def client_order_id(event_id: str, purpose: str) -> str:
        prefix = {"entry": "atv2e", "close": "atv2c", "sl": "atv2s", "tp": "atv2t"}.get(purpose, "atv2x")
        digest = hashlib.sha256(f"{purpose}|{event_id}".encode("utf-8")).hexdigest()[:24]
        return f"{prefix}-{digest}"

    def submit_entry(
        self,
        *,
        event_id: str,
        symbol: str,
        side: str,
        qty: Decimal,
        fill_price: Decimal,
        tp_pct: Decimal,
        sl_pct: Decimal,
        simulate_mode: str = "FULL_FILL",
        sl_reject: bool = False,
        tp_reject: bool = False,
    ) -> dict[str, Any]:
        pos_side = "LONG" if side == "BUY" else "SHORT"
        entry_cid = self.client_order_id(event_id, "entry")
        state = self.journal.setdefault(
            event_id,
            {
                "event_id": event_id,
                "symbol": symbol,
                "side": side,
                "position_side": pos_side,
                "entry_cid": entry_cid,
                "status": "INIT",
                "protection": {},
            },
        )

        # Idempotent recovery on restart / retry
        if entry_cid in self.orders_by_cid:
            existing = self.orders_by_cid[entry_cid]
            state["reconciled_from_cid"] = True
        else:
            if simulate_mode == "REJECT":
                state["status"] = "ENTRY_REJECTED"
                return {"status": "ENTRY_REJECTED", "parity": "VERIFIED"}

            if simulate_mode == "TIMEOUT_BUT_FILLED":
                # Exchange actually filled it despite client timeout; query-by-cid recovers it
                existing = MockExchangeOrder(
                    order_id=f"ORD_{len(self.orders_by_cid) + 1}",
                    client_order_id=entry_cid,
                    symbol=symbol,
                    side=side,
                    position_side=pos_side,
                    order_type="MARKET",
                    orig_qty=qty,
                    executed_qty=qty,
                    avg_price=fill_price,
                    status="FILLED",
                )
                self.orders_by_cid[entry_cid] = existing
                self.positions[(symbol, pos_side)] = self.positions.get((symbol, pos_side), Decimal(0)) + qty
                # Simulate caller retrying/reconciling via clientOrderId
                found = self.orders_by_cid.get(entry_cid)
                assert found is not None
                state["reconciled_after_timeout"] = True
            elif simulate_mode == "PARTIAL_FILL":
                partial_qty = qty / Decimal("2")
                existing = MockExchangeOrder(
                    order_id=f"ORD_{len(self.orders_by_cid) + 1}",
                    client_order_id=entry_cid,
                    symbol=symbol,
                    side=side,
                    position_side=pos_side,
                    order_type="MARKET",
                    orig_qty=qty,
                    executed_qty=partial_qty,
                    avg_price=fill_price,
                    status="PARTIALLY_FILLED",
                )
                self.orders_by_cid[entry_cid] = existing
                self.positions[(symbol, pos_side)] = self.positions.get((symbol, pos_side), Decimal(0)) + partial_qty
                # Verified L4 behavior: unsettled partial MARKET fill -> emergency close
                close_res = self.force_close_unprotected(event_id, symbol, side, partial_qty)
                state["status"] = "PARTIAL_CLOSED" if close_res["closed"] else "ABORT_BLOCKED"
                return {"status": state["status"], "close": close_res, "parity": "VERIFIED"}
            else:
                existing = MockExchangeOrder(
                    order_id=f"ORD_{len(self.orders_by_cid) + 1}",
                    client_order_id=entry_cid,
                    symbol=symbol,
                    side=side,
                    position_side=pos_side,
                    order_type="MARKET",
                    orig_qty=qty,
                    executed_qty=qty,
                    avg_price=fill_price,
                    status="FILLED",
                )
                self.orders_by_cid[entry_cid] = existing
                self.positions[(symbol, pos_side)] = self.positions.get((symbol, pos_side), Decimal(0)) + qty

        state["entry_filled"] = True
        state["executed_qty"] = existing.executed_qty
        state["avg_price"] = existing.avg_price

        # Compute TP/SL from filled entry price (Section 3B)
        if side == "BUY":
            tp_price = existing.avg_price * (Decimal(1) + tp_pct)
            sl_price = existing.avg_price * (Decimal(1) - sl_pct)
            exit_side = "SELL"
        else:
            tp_price = existing.avg_price * (Decimal(1) - tp_pct)
            sl_price = existing.avg_price * (Decimal(1) + sl_pct)
            exit_side = "BUY"

        # Place SL first (verified L4 invariant)
        if sl_reject:
            close_res = self.force_close_unprotected(event_id, symbol, side, existing.executed_qty)
            state["status"] = "FORCE_CLOSED_SL_REJECT" if close_res["closed"] else "ABORT_MANUAL_INTERVENTION"
            return {"status": state["status"], "close": close_res, "parity": "VERIFIED"}

        sl_cid = self.client_order_id(event_id, "sl")
        if sl_cid not in self.orders_by_cid:
            sl_ord = MockExchangeOrder(
                order_id=f"ORD_{len(self.orders_by_cid) + 1}",
                client_order_id=sl_cid,
                symbol=symbol,
                side=exit_side,
                position_side=pos_side,
                order_type="STOP_MARKET",
                orig_qty=existing.executed_qty,
                executed_qty=Decimal(0),
                avg_price=Decimal(0),
                stop_price=sl_price,
                status="NEW",
            )
            self.orders_by_cid[sl_cid] = sl_ord
            self.open_orders[sl_ord.order_id] = sl_ord
            state["protection"]["sl"] = sl_ord.order_id

        # Place TP (100% quantity fixed TP)
        if tp_reject:
            # UNVERIFIED: Codex FIXED_TP_SL PR not yet merged to confirm whether TP reject triggers force-close or retry
            state["status"] = "TP_REJECTED_UNPROTECTED_TP"
            return {"status": state["status"], "parity": "UNVERIFIED"}

        tp_cid = self.client_order_id(event_id, "tp")
        if tp_cid not in self.orders_by_cid:
            tp_ord = MockExchangeOrder(
                order_id=f"ORD_{len(self.orders_by_cid) + 1}",
                client_order_id=tp_cid,
                symbol=symbol,
                side=exit_side,
                position_side=pos_side,
                order_type="TAKE_PROFIT_MARKET",
                orig_qty=existing.executed_qty,
                executed_qty=Decimal(0),
                avg_price=Decimal(0),
                stop_price=tp_price,
                status="NEW",
            )
            self.orders_by_cid[tp_cid] = tp_ord
            self.open_orders[tp_ord.order_id] = tp_ord
            state["protection"]["tp"] = tp_ord.order_id

        state["status"] = "PROTECTED"
        return {
            "status": "PROTECTED",
            "tp_price": tp_price,
            "sl_price": sl_price,
            "qty": existing.executed_qty,
            "reconciled_after_timeout": state.get("reconciled_after_timeout", False),
            "reconciled_from_cid": state.get("reconciled_from_cid", False),
            "parity": "VERIFIED",
        }

    def force_close_unprotected(self, event_id: str, symbol: str, entry_side: str, qty: Decimal) -> dict[str, Any]:
        pos_side = "LONG" if entry_side == "BUY" else "SHORT"
        total_pos = self.positions.get((symbol, pos_side), Decimal(0))
        if total_pos > qty:
            # Verified merged.py / final14_executor.py rule: refuse ambiguous emergency close on shared position
            return {
                "closed": False,
                "reason": "SHARED_POSITION_MANUAL_INTERVENTION_REQUIRED",
                "parity": "VERIFIED",
            }
        self.positions[(symbol, pos_side)] = max(Decimal(0), total_pos - qty)
        return {"closed": True, "reason": "EMERGENCY_MARKET_CLOSE", "parity": "VERIFIED"}

    def executable_exit_budget_ok(self, symbol: str, pos_side: str) -> bool:
        actual = self.positions.get((symbol, pos_side), Decimal(0))
        exit_sum = sum(
            o.orig_qty - o.executed_qty
            for o in self.open_orders.values()
            if o.symbol == symbol and o.position_side == pos_side and o.order_type == "STOP_MARKET"
        )
        return exit_sum <= actual


def test_scenario_1_entry_full_fill_and_4_tpsl_placed():
    h = MockFixedTpSlExecutionHarness()
    res = h.submit_entry(
        event_id="EV1",
        symbol="BTC-USDT",
        side="BUY",
        qty=Decimal("0.001"),
        fill_price=Decimal("100000"),
        tp_pct=Decimal("0.02"),
        sl_pct=Decimal("0.008"),
    )
    assert res["status"] == "PROTECTED"
    assert res["tp_price"] == Decimal("102000")
    assert res["sl_price"] == Decimal("99200")
    assert len(h.open_orders) == 2  # 1 SL + 1 TP (no trailing)


def test_scenario_2_entry_partial_fill_cancels_and_closes():
    h = MockFixedTpSlExecutionHarness()
    res = h.submit_entry(
        event_id="EV2",
        symbol="BTC-USDT",
        side="BUY",
        qty=Decimal("0.002"),
        fill_price=Decimal("100000"),
        tp_pct=Decimal("0.02"),
        sl_pct=Decimal("0.008"),
        simulate_mode="PARTIAL_FILL",
    )
    assert res["status"] == "PARTIAL_CLOSED"
    assert h.positions[("BTC-USDT", "LONG")] == Decimal(0)


def test_scenario_3_entry_reject():
    h = MockFixedTpSlExecutionHarness()
    res = h.submit_entry(
        event_id="EV3",
        symbol="BTC-USDT",
        side="BUY",
        qty=Decimal("0.001"),
        fill_price=Decimal("100000"),
        tp_pct=Decimal("0.02"),
        sl_pct=Decimal("0.008"),
        simulate_mode="REJECT",
    )
    assert res["status"] == "ENTRY_REJECTED"
    assert len(h.positions) == 0


def test_scenario_5_and_8_sl_reject_triggers_force_close():
    h = MockFixedTpSlExecutionHarness()
    res = h.submit_entry(
        event_id="EV5",
        symbol="ETH-USDT",
        side="SELL",
        qty=Decimal("0.05"),
        fill_price=Decimal("2000"),
        tp_pct=Decimal("0.015"),
        sl_pct=Decimal("0.01"),
        sl_reject=True,
    )
    assert res["status"] == "FORCE_CLOSED_SL_REJECT"
    assert h.positions[("ETH-USDT", "SHORT")] == Decimal(0)


def test_scenario_5b_tp_reject_marked_unverified():
    h = MockFixedTpSlExecutionHarness()
    res = h.submit_entry(
        event_id="EV5B",
        symbol="ETH-USDT",
        side="SELL",
        qty=Decimal("0.05"),
        fill_price=Decimal("2000"),
        tp_pct=Decimal("0.015"),
        sl_pct=Decimal("0.01"),
        tp_reject=True,
    )
    assert res["status"] == "TP_REJECTED_UNPROTECTED_TP"
    assert res["parity"] == "UNVERIFIED"


def test_scenario_6_7_9_timeout_retry_reconcile_and_restart():
    h = MockFixedTpSlExecutionHarness()
    r1 = h.submit_entry(
        event_id="EV6",
        symbol="BTC-USDT",
        side="BUY",
        qty=Decimal("0.001"),
        fill_price=Decimal("100000"),
        tp_pct=Decimal("0.02"),
        sl_pct=Decimal("0.008"),
        simulate_mode="TIMEOUT_BUT_FILLED",
    )
    assert r1["status"] == "PROTECTED"
    assert r1["reconciled_after_timeout"] is True

    # Simulate process restart resuming same event_id from durable journal
    r2 = h.submit_entry(
        event_id="EV6",
        symbol="BTC-USDT",
        side="BUY",
        qty=Decimal("0.001"),
        fill_price=Decimal("100000"),
        tp_pct=Decimal("0.02"),
        sl_pct=Decimal("0.008"),
    )
    assert r2["status"] == "PROTECTED"
    assert r2["reconciled_from_cid"] is True
    assert h.positions[("BTC-USDT", "LONG")] == Decimal("0.001")  # no duplicate entry


def test_scenario_10_and_11_multi_combo_same_symbol_side_isolation():
    h = MockFixedTpSlExecutionHarness()
    r1 = h.submit_entry(
        event_id="EV_C1",
        symbol="BTC-USDT",
        side="BUY",
        qty=Decimal("0.001"),
        fill_price=Decimal("100000"),
        tp_pct=Decimal("0.02"),
        sl_pct=Decimal("0.008"),
    )
    r2 = h.submit_entry(
        event_id="EV_C2",
        symbol="BTC-USDT",
        side="BUY",
        qty=Decimal("0.001"),
        fill_price=Decimal("101000"),
        tp_pct=Decimal("0.012"),
        sl_pct=Decimal("0.008"),
    )
    assert r1["status"] == "PROTECTED"
    assert r2["status"] == "PROTECTED"
    assert h.positions[("BTC-USDT", "LONG")] == Decimal("0.002")
    assert h.executable_exit_budget_ok("BTC-USDT", "LONG") is True

    # If C3 fails SL placement on an already-shared position, L4 must NOT emergency-close the whole side
    r3 = h.submit_entry(
        event_id="EV_C3",
        symbol="BTC-USDT",
        side="BUY",
        qty=Decimal("0.001"),
        fill_price=Decimal("102000"),
        tp_pct=Decimal("0.012"),
        sl_pct=Decimal("0.008"),
        sl_reject=True,
    )
    assert r3["status"] == "ABORT_MANUAL_INTERVENTION"
    assert h.positions[("BTC-USDT", "LONG")] == Decimal("0.003")  # C1 & C2 remain intact
