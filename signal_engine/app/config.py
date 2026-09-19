from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None else float(raw)


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None else int(raw)


@dataclass(frozen=True)
class Settings:
    bingx_base_url: str = os.getenv("BINGX_BASE_URL", "https://open-api.bingx.com")
    bingx_api_key: str = os.getenv("BINGX_API_KEY", "")
    bingx_api_secret: str = os.getenv("BINGX_API_SECRET", "")
    symbols: tuple[str, ...] = tuple(
        x.strip().upper()
        for x in os.getenv("SYMBOLS", "BTC-USDT,ETH-USDT").split(",")
        if x.strip()
    )

    smc_mode: str = os.getenv("SMC_MODE", "Veto Only")
    smc_swing_len: int = _int("SMC_SWING_LEN", 50)
    smc_confluence: bool = _bool("SMC_CONFLUENCE", False)
    sl_pct: float = _float("SL_PERCENT", 0.9) / 100.0
    tp_pct: float = _float("TP_PERCENT", 1.1) / 100.0

    dry_run: bool = _bool("DRY_RUN", True)
    auto_scheduler: bool = _bool("AUTO_SCHEDULER", True)
    scheduler_second: int = _int("SCHEDULER_SECOND", 8)

    webhook_1: str = os.getenv("ORDER_WEBHOOK_1", "")
    webhook_1_usdt: float = _float("ORDER_WEBHOOK_1_USDT", 700.0)
    webhook_2: str = os.getenv("ORDER_WEBHOOK_2", "")
    webhook_2_usdt: float = _float("ORDER_WEBHOOK_2_USDT", 100.0)

    discord_enabled: bool = _bool("DISCORD_ENABLED", True)
    discord_on_dry_run: bool = _bool("DISCORD_ON_DRY_RUN", True)
    discord_webhook_btc: str = os.getenv("DISCORD_WEBHOOK_BTC", "")
    discord_webhook_eth: str = os.getenv("DISCORD_WEBHOOK_ETH", "")
    discord_webhook_default: str = os.getenv("DISCORD_WEBHOOK_DEFAULT", "")

    adjust_tp_sl_bps: float = _float("ADJUST_TP_SL_BPS", 10.0)
    legacy_rounding: bool = _bool("LEGACY_ROUNDING", True)

    state_db: str = os.getenv("STATE_DB", "state.db")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    limit_15m: int = _int("LIMIT_15M", 1000)
    limit_1h: int = _int("LIMIT_1H", 600)
    limit_4h: int = _int("LIMIT_4H", 350)
    limit_6h: int = _int("LIMIT_6H", 250)
