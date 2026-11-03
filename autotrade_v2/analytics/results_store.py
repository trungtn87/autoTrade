"""Persist one normalized result per V2 intent; no execution table writes."""
import json
from datetime import datetime, timezone
from .bridge import case_name
from sqlalchemy import JSON, DateTime, String, select, text
from sqlalchemy.orm import Mapped, mapped_column, DeclarativeBase
from sqlalchemy import MetaData
from dashboard.autotrade_live.db import Base, Order, Fill, ForceOrder, get_sessionmaker
from dashboard.autotrade_live.exits import build_exit_events, extract_exit_spec
from .reconciler import reconcile


class ResultBase(DeclarativeBase):
    metadata = MetaData(schema=Base.metadata.schema)


class TradeResult(ResultBase):
    __tablename__ = 'trade_results'
    event_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def result_specs(states):
    exact, ledger = [], []
    for event_id, payload in states:
        spec = extract_exit_spec(payload, event_id)
        if spec is not None:
            exact.append(spec)
            ledger.append(spec)
            continue
        try:
            state = json.loads(payload) if isinstance(payload, str) else payload
            intent = state.get('intent')
            if not isinstance(intent, dict) or not intent:
                continue
            try:
                combo = case_name(intent['combo'])
            except (KeyError, ValueError, TypeError):
                combo = None
            ledger.append(dict(event_id=str(event_id), entry_order_id=str(state.get('order_id') or ''),
                               symbol=str(intent.get('symbol') or '').upper(),
                               side=str(intent.get('side') or '').upper(), combo=combo))
        except (ValueError, TypeError, AttributeError):
            continue
    return exact, ledger


def reconcile_database(engine):
    # Preserve all intents: the legacy loader deduplicates by entry ID and
    # would hide an ambiguous mapping from the reconciler.
    with engine.connect() as conn:
        states = conn.execute(text("SELECT event_id, payload FROM public.processed_targets WHERE target = 'bingx_state' AND payload IS NOT NULL")).all()
    specs, ledger_specs = result_specs(states)
    Session = get_sessionmaker(engine)
    with Session() as db:
        orders = db.scalars(select(Order)).all()
        fills = db.scalars(select(Fill)).all()
        forces = db.scalars(select(ForceOrder)).all()
    results = reconcile(ledger_specs, orders, fills, build_exit_events(orders, fills, specs, forces))
    with Session.begin() as db:
        for result in results:
            db.merge(TradeResult(event_id=result['event_id'], payload=result,
                                 updated_at=datetime.now(timezone.utc)))
    return len(results)
