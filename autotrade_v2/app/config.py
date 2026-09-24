from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    symbols: tuple[str, ...] = tuple(
        x.strip().upper()
        for x in os.getenv("SYMBOLS", "BTC-USDT,ETH-USDT").split(",")
        if x.strip()
    )
    bingx_base_url: str = os.getenv("BINGX_BASE_URL", "https://open-api.bingx.com").rstrip("/")
    bingx_api_key: str = os.getenv("BINGX_API_KEY", "")
    bingx_api_secret: str = os.getenv("BINGX_API_SECRET", "")

    database_url: str = os.getenv("DATABASE_URL", "")
    state_db: str = os.getenv("STATE_DB", "autotrade_v2.db")

    market_limit_15m: int = 1000
    candle_keep_15m: int = 3400
    required_15m: int = 3400

    auto_scheduler: bool = _bool("AUTO_SCHEDULER", False)
    scheduler_second: int = _int("SCHEDULER_SECOND", 8)

    execution_enabled: bool = _bool("EXECUTION_ENABLED", False)
    dry_run: bool = _bool("DRY_RUN", True)

    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()


def validate_settings(settings: Settings) -> None:
    if settings.market_limit_15m != 1000:
        raise ValueError("market_limit_15m is locked to 1000")
    if settings.candle_keep_15m < settings.required_15m:
        raise ValueError("candle_keep_15m must be >= required_15m")
    if settings.execution_enabled and not settings.dry_run:
        if not settings.bingx_api_key or not settings.bingx_api_secret:
            raise ValueError("live execution requires BINGX_API_KEY and BINGX_API_SECRET")
