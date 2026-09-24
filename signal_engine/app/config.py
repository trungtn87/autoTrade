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

    # Production policy: this engine is always LIVE. Safety is enforced by
    # per-scan/per-order gates, never by silently switching to DRY_RUN.
    dry_run: bool = False
    auto_scheduler: bool = _bool("AUTO_SCHEDULER", True)
    scheduler_second: int = _int("SCHEDULER_SECOND", 8)

    # FINAL14 production sizing is locked: 100 USDT notional at 50x leverage.
    order_margin_usdt: float = 2.0
    leverage: int = 50

    discord_enabled: bool = _bool("DISCORD_ENABLED", True)
    discord_on_dry_run: bool = _bool("DISCORD_ON_DRY_RUN", True)
    # Three dedicated Discord destinations: BTC trades, ETH trades, errors.
    discord_webhook_btc: str = os.getenv("DISCORD_WEBHOOK_BTC", "")
    discord_webhook_eth: str = os.getenv("DISCORD_WEBHOOK_ETH", "")
    discord_webhook_error: str = os.getenv("DISCORD_WEBHOOK_ERROR", "")
    discord_log_enabled: bool = True
    discord_periodic_log_enabled: bool = False
    discord_log_level: str = "ERROR"
    discord_startup_test: bool = _bool("DISCORD_STARTUP_TEST", False)

    adjust_tp_sl_bps: float = 0.0
    legacy_rounding: bool = False

    state_db: str = os.getenv("STATE_DB", "state.db")
    database_url: str = os.getenv("DATABASE_URL", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    scan_token: str = os.getenv("SCAN_TOKEN", "")

    bootstrap_limit_15m: int = _int("BOOTSTRAP_LIMIT_15M", 3400)
    # Live market-data policy: one broad overlap request per symbol/scan.
    # Keep this fixed so Render environment leftovers cannot silently restore limit=2.
    live_limit_15m: int = 1000
    recovery_limit_15m: int = 1000  # compatibility only; live recovery requests are disabled
    candle_keep_15m: int = _int("CANDLE_KEEP_15M", 3400)


def _is_http_url(value: str) -> bool:
    v = (value or "").strip().lower()
    return v.startswith("https://") or v.startswith("http://")


def validate_settings(settings: Settings) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) without exposing secrets."""
    import re

    errors: list[str] = []
    warnings: list[str] = []

    if not settings.bingx_api_key:
        errors.append("BINGX_API_KEY is empty")
    if not settings.bingx_api_secret:
        errors.append("BINGX_API_SECRET is empty")
    if not _is_http_url(settings.bingx_base_url):
        errors.append("BINGX_BASE_URL must start with http:// or https://")

    if not settings.symbols:
        errors.append("SYMBOLS is empty")
    for symbol in settings.symbols:
        if not re.fullmatch(r"[A-Z0-9]+-[A-Z]+", symbol):
            errors.append(f"Invalid symbol format: {symbol}")

    allowed_smc = {"off", "strict", "veto only"}
    if settings.smc_mode.strip().lower() not in allowed_smc:
        errors.append(
            f"SMC_MODE must be one of Off/Strict/Veto Only, got {settings.smc_mode!r}"
        )
    if settings.smc_swing_len < 5:
        errors.append("SMC_SWING_LEN must be >= 5")

    if not (0 < settings.sl_pct < 0.20):
        errors.append("SL_PERCENT must be > 0 and < 20%")
    if not (0 < settings.tp_pct < 0.20):
        errors.append("TP_PERCENT must be > 0 and < 20%")
    if not (-500 <= settings.adjust_tp_sl_bps <= 500):
        errors.append("ADJUST_TP_SL_BPS must be between -500 and 500")

    if not (0 <= settings.scheduler_second <= 59):
        errors.append("SCHEDULER_SECOND must be between 0 and 59")
    if settings.live_limit_15m != 1000:
        errors.append("LIVE_LIMIT_15M is locked to 1000 for the single-window live data path")
    if settings.bootstrap_limit_15m < 24:
        errors.append("BOOTSTRAP_LIMIT_15M must be >= 24")
    # recovery_limit_15m remains only for backward compatibility with old
    # config snapshots; no live code is allowed to issue recovery requests.
    if settings.candle_keep_15m < settings.bootstrap_limit_15m:
        errors.append("CANDLE_KEEP_15M must be >= BOOTSTRAP_LIMIT_15M")

    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if settings.discord_log_level.upper() not in valid_levels:
        errors.append(
            f"DISCORD_LOG_LEVEL must be one of {sorted(valid_levels)}, got {settings.discord_log_level!r}"
        )
    if settings.log_level.upper() not in valid_levels:
        errors.append(
            f"LOG_LEVEL must be one of {sorted(valid_levels)}, got {settings.log_level!r}"
        )

    for name, url in (
        ("DISCORD_WEBHOOK_BTC", settings.discord_webhook_btc),
        ("DISCORD_WEBHOOK_ETH", settings.discord_webhook_eth),
        ("DISCORD_WEBHOOK_ERROR", settings.discord_webhook_error),
    ):
        if url and not _is_http_url(url):
            errors.append(f"{name} must start with http:// or https://")

    if settings.order_margin_usdt <= 0:
        errors.append("BINGX_ORDER_MARGIN_USDT must be > 0")
    if not (1 <= settings.leverage <= 125):
        errors.append("BINGX_LEVERAGE must be between 1 and 125")
    if not settings.database_url:
        errors.append(
            "LIVE engine requires DATABASE_URL for persistent Postgres state"
        )
    if settings.discord_enabled and not settings.discord_webhook_btc:
        warnings.append("DISCORD_WEBHOOK_BTC is empty; BTC trade notifications are disabled")
    if settings.discord_enabled and not settings.discord_webhook_eth:
        warnings.append("DISCORD_WEBHOOK_ETH is empty; ETH trade notifications are disabled")
    if settings.discord_log_enabled and not settings.discord_webhook_error:
        warnings.append("DISCORD_WEBHOOK_ERROR is empty; error notifications are disabled")
    if settings.bootstrap_limit_15m < 3400:
        warnings.append(
            f"BOOTSTRAP_LIMIT_15M={settings.bootstrap_limit_15m} is below 3400; runtime will use 3400 for FINAL14 warmup"
        )
    if not settings.scan_token:
        warnings.append("SCAN_TOKEN is empty; manual market/scan endpoints will stay disabled")

    return errors, warnings


def safe_config_snapshot(settings: Settings) -> dict:
    """Non-secret startup snapshot suitable for Render Logs."""
    return {
        "symbols": list(settings.symbols),
        "bingx_base_url": settings.bingx_base_url,
        "bingx_credentials_present": bool(
            settings.bingx_api_key and settings.bingx_api_secret
        ),
        "market_mode": "15m_only_incremental",
        "bootstrap_limit_15m": settings.bootstrap_limit_15m,
        "bootstrap_limit_15m_effective": max(3400, settings.bootstrap_limit_15m),
        "bootstrap_page_limit_15m": 1000,
        "live_limit_15m": settings.live_limit_15m,
        "recovery_requests_enabled": False,
        "candle_keep_15m": settings.candle_keep_15m,
        "auto_scheduler": settings.auto_scheduler,
        "scheduler_minutes_utc": [0, 15, 30, 45],
        "scheduler_second": settings.scheduler_second,
        "smc_mode": settings.smc_mode,
        "smc_swing_len": settings.smc_swing_len,
        "smc_confluence": settings.smc_confluence,
        "sl_pct": settings.sl_pct,
        "tp_pct": settings.tp_pct,
        "adjust_tp_sl_bps": settings.adjust_tp_sl_bps,
        "legacy_rounding": settings.legacy_rounding,
        "dry_run": settings.dry_run,
        "execution_ready": bool(settings.bingx_api_key and settings.bingx_api_secret and settings.database_url),
        "order_execution": {
            "mode": "final14_hard_tp_sl",
            "configured": bool(settings.bingx_api_key and settings.bingx_api_secret),
            "margin_usdt": settings.order_margin_usdt,
            "leverage": settings.leverage,
            "target_notional_usdt": settings.order_margin_usdt * settings.leverage,
            "margin_mode": "SEPARATE_ISOLATED",
            "one_position_per_symbol_combo": True,
            "protection_mode": "attached_hard_tp_sl",
            "partial_exit": False,
            "trailing": False,
        },
        "discord": {
            "enabled": settings.discord_enabled,
            "on_dry_run": settings.discord_on_dry_run,
            "btc_configured": bool(settings.discord_webhook_btc),
            "eth_configured": bool(settings.discord_webhook_eth),
            "error_configured": bool(settings.discord_webhook_error),
            "log_enabled": settings.discord_log_enabled,
            "periodic_log_enabled": settings.discord_periodic_log_enabled,
            "log_level": settings.discord_log_level,
            "startup_test": settings.discord_startup_test,
        },
        "state_db": settings.state_db,
        "state_backend": "postgres" if settings.database_url else "sqlite",
        "log_level": settings.log_level,
        "manual_scan_enabled": bool(settings.scan_token),
    }


def resolved_discord_log_webhook(settings: Settings) -> str:
    """Error/diagnostic logs go only to the dedicated error channel."""
    return settings.discord_webhook_error
