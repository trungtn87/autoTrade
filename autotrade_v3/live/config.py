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

    candle_keep_15m: int = 3400
    required_15m: int = 3400
    historical_request_limit: int = 1000
    historical_min_interval_ms: int = 1100

    # Current execution mechanism (unchanged from production V2):
    # 100 USDT notional, x50, SL on the full fill, 50% fixed TP + 50% trailing.
    notional_usdt: float = 100.0
    leverage: int = 50
    tp_trailing_split_enabled: bool = field(default_factory=lambda: _bool("TP_TRAILING_SPLIT_ENABLED", True))
    trailing_activation_pct: float = field(default_factory=lambda: float(os.getenv("TP_TRAILING_ACTIVATION_PCT", "0.005")))
    trailing_callback_rate: float = field(default_factory=lambda: float(os.getenv("TP_TRAILING_CALLBACK_RATE", "0.005")))

    discord_enabled: bool = field(default_factory=lambda: _bool("DISCORD_ENABLED", True))
    discord_webhook_btc: str = field(default_factory=lambda: os.getenv("DISCORD_WEBHOOK_BTC", ""))
    discord_webhook_eth: str = field(default_factory=lambda: os.getenv("DISCORD_WEBHOOK_ETH", ""))
    discord_webhook_error: str = field(default_factory=lambda: os.getenv("DISCORD_WEBHOOK_ERROR", ""))

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
