"""PostgreSQL adapter for new L3 Entry ledger.

The caller supplies DATABASE_URL. This module does not perform schema changes;
the five-layer schema is administered separately by the database owner.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json

from ..contracts import TradeIntent
from .l4_entry import EntryEvidence


class PgEntryLedger:
    def __init__(self, database_url: str):
        if not database_url:
            raise ValueError("DATABASE_URL required for durable Entry identity")
        self.database_url = database_url

    def _connect(self):
        import psycopg
        return psycopg.connect(self.database_url, connect_timeout=5)

    def claim(self, intent: TradeIntent, client_order_id: str) -> bool:
        """Atomic INSERT gives at most one worker permission to POST a signal."""
        close_time = datetime.fromtimestamp(intent.close_time / 1000, tz=timezone.utc)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO autotrade_analytics.strategy_signals
                   (event_id,symbol,combo,side,signal_time,entry,tp,sl)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (event_id) DO NOTHING""",
                (intent.event_id, intent.symbol, str(intent.combo), intent.side,
                 close_time, Decimal(str(intent.entry)), Decimal(str(intent.tp)),
                 Decimal(str(intent.sl))),
            )
            row = conn.execute(
                """INSERT INTO autotrade_analytics.execution_orders
                   (event_id,client_order_id,step,status)
                   VALUES (%s,%s,'ENTRY','RESERVED')
                   ON CONFLICT (client_order_id) DO NOTHING RETURNING id""",
                (intent.event_id, client_order_id),
            ).fetchone()
            if row is None:
                existing = conn.execute(
                    """SELECT event_id,step FROM autotrade_analytics.execution_orders
                       WHERE client_order_id=%s""",
                    (client_order_id,),
                ).fetchone()
                if existing != (intent.event_id, "ENTRY"):
                    raise RuntimeError("client order ID collision")
        return row is not None

    def mark_sending(self, event_id: str, client_order_id: str) -> None:
        """Commit the irrevocable intent *before* performing the MARKET POST."""
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE autotrade_analytics.execution_orders
                   SET status='SENDING', updated_at=now()
                   WHERE event_id=%s AND client_order_id=%s
                         AND step='ENTRY' AND status='RESERVED'""",
                (event_id, client_order_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("Entry send claim lost; MARKET not submitted")

    def record(self, event_id: str, evidence: EntryEvidence, status: str) -> None:
        if status not in {
            "ENTRY_FILLED", "ENTRY_PARTIAL", "ENTRY_NOT_FILLED", "ENTRY_PENDING"
        }:
            raise ValueError("invalid Entry status")
        body = json.dumps(evidence.raw_response, default=str) if evidence.raw_response else None
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE autotrade_analytics.execution_orders
                   SET status=%s, bingx_order_id=COALESCE(%s,bingx_order_id),
                       executed_qty=COALESCE(%s,executed_qty),
                       avg_price=COALESCE(%s,avg_price),
                       bingx_endpoint='/openApi/swap/v2/trade/order',
                       bingx_queried_at=now(),
                       raw_response=COALESCE(%s::jsonb,raw_response),
                       updated_at=now()
                   WHERE event_id=%s AND client_order_id=%s AND step='ENTRY'
                         AND NOT (status='ENTRY_FILLED' AND %s <> 'ENTRY_FILLED')""",
                (status, evidence.order_id, evidence.executed_qty,
                 evidence.avg_price, body, event_id, evidence.client_order_id, status),
            )
            if cur.rowcount == 0:
                # A previous FILLED proof must not be overwritten by a stale GET.
                current = conn.execute(
                    """SELECT status FROM autotrade_analytics.execution_orders
                       WHERE event_id=%s AND client_order_id=%s AND step='ENTRY'""",
                    (event_id, evidence.client_order_id),
                ).fetchone()
                if not current or current[0] != "ENTRY_FILLED":
                    raise RuntimeError("Entry evidence row missing")
