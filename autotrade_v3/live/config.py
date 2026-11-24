"""V3 settings: the same environment variables as V2, nothing new to configure."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _symbols() -> tuple[str, ...]:
    return tuple(x.strip().upper() for x in os.getenv("SYMBOLS", "BTC-USDT,ETH-USDT").split(",") if x.strip())


@dataclass(frozen=True)
class V3Settings:
    symbols: tuple[str, ...] = field(default_factory=_symbols)
    bingx_base_url: str = field(default_factory=lambda: os.getenv("BINGX_BASE_URL", "https://open-api.bingx.com").rstrip("/"))
    bingx_api_key: str = field(default_factory=lambda: os.getenv("BINGX_API_KEY", ""))
    bingx_api_secret: str = field(default_factory=lambda: os.getenv("BINGX_API_SECRET", ""))
    bingx_ws_url: str = field(default_factory=lambda: os.getenv("BINGX_WS_URL", "wss://open-api-swap.bingx.com/swap-market"))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))

    # Database retention is deliberately independent from the candles handed to L2.
    # Environment values may increase retention, but may never downgrade the approved
    # PLUS44 capacity (in particular, an old live ETH=12000 value is harmless).
    candle_retention_btc_15m: int = 13_440
    candle_retention_eth_15m: int = field(
        default_factory=lambda: max(25_920, int(os.getenv("V3_CANDLE_KEEP_ETH_15M", "25920")))
    )
    # Existing PRUNE23 signal windows. Do not couple these to database retention.
    signal_window_btc_15m: int = 3_400
    signal_window_eth_15m: int = 12_000
    required_15m: int = 3400

    def candle_retention_for(self, symbol: str) -> int:
        return (self.candle_retention_eth_15m if str(symbol).upper() == "ETH-USDT"
                else self.candle_retention_btc_15m)

    def signal_window_for(self, symbol: str) -> int:
        return (self.signal_window_eth_15m if str(symbol).upper() == "ETH-USDT"
                else self.signal_window_btc_15m)
    historical_request_limit: int = 1000
    historical_min_interval_ms: int = 1100

    # Exit policy FIXED_TP_SL for every new entry: 100 USDT notional, x50, one fixed TP and
    # one fixed SL on the full real fill. No trailing, no split, no breakeven.
    notional_usdt: float = 100.0
    leverage: int = 50
    # ETH-USDT/C1 only: H1_BOTH_1 L2 filter (Lab P3). off = pure V2 output (rollback).

    discord_enabled: bool = field(default_factory=lambda: _bool("DISCORD_ENABLED", True))
    discord_webhook_btc: str = field(default_factory=lambda: os.getenv("DISCORD_WEBHOOK_BTC", ""))
    discord_webhook_eth: str = field(default_factory=lambda: os.getenv("DISCORD_WEBHOOK_ETH", ""))
    discord_webhook_error: str = field(default_factory=lambda: os.getenv("DISCORD_WEBHOOK_ERROR", ""))

    # Leftover protection cleanup after a combo closes: live (cancel, default) | shadow (record only) | off.
    leftover_cleanup_mode: str = field(default_factory=lambda: os.getenv("V3_LEFTOVER_CLEANUP_MODE", "live").strip().lower())

    # SL amendments require controlled BingX STOP_MARKET verification before opt-in.
    # shadow records proposed exact-ID changes; live sends in-place amendments only.
    sl_resize_mode: str = field(default_factory=lambda: os.getenv("V3_SL_RESIZE_MODE", "shadow").strip().lower())
    sl_resize_poll_sec: int = field(default_factory=lambda: max(20, int(os.getenv("V3_SL_RESIZE_POLL_SEC", "30"))))

    # L2 strategy registry: the only value is V3_LIVE_23_LAB_11_V1 (default, Lab 11 PRUNE23 flat engine).
    strategy_registry: str = field(default_factory=lambda: os.getenv("V3_STRATEGY_REGISTRY", "V3_LIVE_23_LAB_11_V1").strip().upper())

    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    @property
    def execution_enabled(self) -> bool:
        """Live mode = exchange credentials present. No other switch or gate."""
        return bool(self.bingx_api_key and self.bingx_api_secret)

    @property
    def websocket_enabled(self) -> bool:
        return bool(self.database_url)

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgres://", "postgresql://"))
