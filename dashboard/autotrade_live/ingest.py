"""Optional Combo-tag receiver. Does NOT provide any trade/order placement endpoint."""
from __future__ import annotations
import hmac
import os
import re
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from .db import SignalLink, get_engine, get_sessionmaker, init_db

app = FastAPI(title='Autotrade Combo Tag Receiver', docs_url=None, redoc_url=None)


class SignalEvent(BaseModel):
    entry_order_id: str = Field(min_length=1, max_length=90)
    combo: str = Field(min_length=1, max_length=100)
    signal_id: str = Field(default='', max_length=120)
    symbol: str = Field(default='', max_length=40)
    side: str = Field(default='', max_length=12)
    signal_time_ms: int = Field(default=0, ge=0)


@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'combo-tag-receiver'}


@app.post('/v1/signals', status_code=202)
def signal_event(event: SignalEvent, x_collector_token: str | None = Header(default=None)):
    expected = os.environ.get('COLLECTOR_TOKEN', '')
    if not expected or not x_collector_token or not hmac.compare_digest(expected, x_collector_token):
        raise HTTPException(status_code=401, detail='Unauthorized')
    if not re.fullmatch(r'\d+', event.entry_order_id):
        raise HTTPException(status_code=422, detail='entry_order_id must be a numeric string')
    if event.symbol and not re.fullmatch(r'[A-Z0-9]+-[A-Z]+', event.symbol):
        raise HTTPException(status_code=422, detail='Invalid symbol')
    if event.side and event.side not in ('BUY', 'SELL'):
        raise HTTPException(status_code=422, detail='Invalid side')
    engine = get_engine()
    init_db(engine)
    Session = get_sessionmaker(engine)
    with Session.begin() as db:
        previous = db.get(SignalLink, event.entry_order_id)
        if previous and previous.combo != event.combo:
            raise HTTPException(status_code=409, detail='orderId already linked to a different combo')
        db.merge(SignalLink(**event.model_dump(), source='webhook'))
    return {'accepted': True, 'entry_order_id': event.entry_order_id}
