"""Persistent read-only trading mirror; no order placing functionality."""
from __future__ import annotations
import os
from datetime import datetime, timezone
from sqlalchemy import JSON, BigInteger, DateTime, Float, Integer, String, Text, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


# Keep analytics data outside Supabase's public/Data-API schemas.
# Local SQLite tests leave DB_SCHEMA unset.
ANALYTICS_SCHEMA = os.environ.get('DB_SCHEMA', '').strip() or None

class Base(DeclarativeBase):
    metadata = MetaData(schema=ANALYTICS_SCHEMA)


class Order(Base):
    __tablename__ = 'bingx_orders'
    order_id: Mapped[str] = mapped_column(String(90), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    side: Mapped[str] = mapped_column(String(12), default='')
    position_side: Mapped[str] = mapped_column(String(12), default='')
    type: Mapped[str] = mapped_column(String(45), default='')
    status: Mapped[str] = mapped_column(String(40), default='')
    price: Mapped[float] = mapped_column(Float, default=0)
    avg_price: Mapped[float] = mapped_column(Float, default=0)
    quantity: Mapped[float] = mapped_column(Float, default=0)
    filled_qty: Mapped[float] = mapped_column(Float, default=0)
    created_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class Fill(Base):
    __tablename__ = 'bingx_fills'
    trade_id: Mapped[str] = mapped_column(String(90), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(90), index=True)
    symbol: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    side: Mapped[str] = mapped_column(String(12), default='')
    position_side: Mapped[str] = mapped_column(String(12), default='')
    qty: Mapped[float] = mapped_column(Float, default=0)
    price: Mapped[float] = mapped_column(Float, default=0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0)
    fee: Mapped[float] = mapped_column(Float, default=0)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class Income(Base):
    __tablename__ = 'bingx_income'
    income_key: Mapped[str] = mapped_column(String(180), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), default='')
    income_type: Mapped[str] = mapped_column(String(45), index=True)
    amount: Mapped[float] = mapped_column(Float, default=0)
    asset: Mapped[str] = mapped_column(String(15), default='USDT')
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    trade_id: Mapped[str] = mapped_column(String(90), default='')
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class ForceOrder(Base):
    __tablename__ = 'bingx_force_orders'
    force_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(90), default='')
    symbol: Mapped[str] = mapped_column(String(40), default='')
    reason: Mapped[str] = mapped_column(String(50), default='LIQUIDATION')
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class PositionHistory(Base):
    __tablename__ = 'bingx_position_history'
    position_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), default='')
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class SignalLink(Base):
    """Created by Autotrade after confirming an ENTRY order id; never guessed."""
    __tablename__ = 'signal_links'
    entry_order_id: Mapped[str] = mapped_column(String(90), primary_key=True)
    combo: Mapped[str] = mapped_column(String(100), index=True)
    signal_id: Mapped[str] = mapped_column(String(120), default='')
    symbol: Mapped[str] = mapped_column(String(40), default='')
    side: Mapped[str] = mapped_column(String(12), default='')
    signal_time_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    source: Mapped[str] = mapped_column(String(30), default='csv')


class SyncRun(Base):
    __tablename__ = 'sync_runs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='running')
    details: Mapped[str] = mapped_column(Text, default='')


def get_engine():
    url = os.environ.get('DATABASE_URL', 'sqlite:///autotrade_live.db')
    # Some hosted Postgres URLs still use the deprecated postgres:// scheme.
    if url.startswith('postgres://'):
        url = 'postgresql+psycopg://' + url[len('postgres://'):]
    elif url.startswith('postgresql://'):
        url = 'postgresql+psycopg://' + url[len('postgresql://'):]
    args = {'check_same_thread': False} if url.startswith('sqlite') else {}
    if url.startswith('postgresql+psycopg:') and '6543' in url:
        # Supabase transaction pooler does not support prepared statements.
        args['prepare_threshold'] = None
    return create_engine(url, pool_pre_ping=True, connect_args=args)


def init_db(engine):
    Base.metadata.create_all(engine)


def get_sessionmaker(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)
