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

    # Single-account direct BingX execution with fixed margin per order.
    # 1 USDT margin at 100x leverage -> about 100 USDT position notional.
    order_margin_usdt: float = _float("BINGX_ORDER_MARGIN_USDT", 1.0)
    leverage: int = _int("BINGX_LEVERAGE", 100)

    discord_enabled: bool = _bool("DISCORD_ENABLED", True)
    discord_on_dry_run: bool = _bool("DISCORD_ON_DRY_RUN", True)
    discord_webhook_btc: str = os.getenv("DISCORD_WEBHOOK_BTC", "")
    discord_webhook_eth: str = os.getenv("DISCORD_WEBHOOK_ETH", "")
    discord_webhook_default: str = os.getenv("DISCORD_WEBHOOK_DEFAULT", "")
    # Operational errors must always reach Discord. Periodic scan reports
    # remain independently disabled.
    discord_log_enabled: bool = True
    discord_periodic_log_enabled: bool = _bool("DISCORD_PERIODIC_LOG_ENABLED", False)
    discord_log_webhook: str = os.getenv("DISCORD_LOG_WEBHOOK", "")
    discord_log_level: str = "ERROR"
    discord_startup_test: bool = _bool("DISCORD_STARTUP_TEST", False)

    adjust_tp_sl_bps: float = _float("ADJUST_TP_SL_BPS", 10.0)
    legacy_rounding: bool = _bool("LEGACY_ROUNDING", True)

    state_db: str = os.getenv("STATE_DB", "state.db")
    database_url: str = os.getenv("DATABASE_URL", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    scan_token: str = os.getenv("SCAN_TOKEN", "")

    bootstrap_limit_15m: int = _int("BOOTSTRAP_LIMIT_15M", 1000)
    live_limit_15m: int = _int("LIVE_LIMIT_15M", 2)
    recovery_limit_15m: int = _int("RECOVERY_LIMIT_15M", 8)
    candle_keep_15m: int = _int("CANDLE_KEEP_15M", 12000)


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
    if not (2 <= settings.live_limit_15m <= 20):
        errors.append("LIVE_LIMIT_15M must be between 2 and 20")
    if settings.bootstrap_limit_15m < 24:
        errors.append("BOOTSTRAP_LIMIT_15M must be >= 24")
    elif settings.bootstrap_limit_15m > 1000:
        warnings.append(
            f"BOOTSTRAP_LIMIT_15M={settings.bootstrap_limit_15m} exceeds safe max 1000; runtime will clamp to 1000"
        )
    if not (settings.live_limit_15m <= settings.recovery_limit_15m <= 100):
        errors.append("RECOVERY_LIMIT_15M must be >= LIVE_LIMIT_15M and <= 100")
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
        ("DISCORD_WEBHOOK_DEFAULT", settings.discord_webhook_default),
        ("DISCORD_LOG_WEBHOOK", settings.discord_log_webhook),
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
    if settings.discord_enabled and not (
        settings.discord_webhook_btc
        or settings.discord_webhook_eth
        or settings.discord_webhook_default
    ):
        warnings.append("DISCORD_ENABLED=true but no Discord webhook is configured")
    if settings.discord_log_enabled and not (
        settings.discord_log_webhook
        or settings.discord_webhook_default
        or settings.discord_webhook_btc
        or settings.discord_webhook_eth
    ):
        warnings.append("DISCORD_LOG_ENABLED=true but no Discord destination is configured")
    if settings.bootstrap_limit_15m < 2400:
        warnings.append(
            "15m bootstrap is shorter than 2400 candles; H4 EMA150 will not have a full 150 native H4-bar warmup initially"
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
        "bootstrap_limit_15m_effective": min(settings.bootstrap_limit_15m, 1000),
        "live_limit_15m": settings.live_limit_15m,
        "recovery_limit_15m": settings.recovery_limit_15m,
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
            "mode": "direct_bingx_single_account_fixed_margin",
            "configured": bool(settings.bingx_api_key and settings.bingx_api_secret),
            "margin_usdt": settings.order_margin_usdt,
            "leverage": settings.leverage,
            "target_notional_usdt": settings.order_margin_usdt * settings.leverage,
        },
        "discord": {
            "enabled": settings.discord_enabled,
            "on_dry_run": settings.discord_on_dry_run,
            "btc_configured": bool(settings.discord_webhook_btc),
            "eth_configured": bool(settings.discord_webhook_eth),
            "default_configured": bool(settings.discord_webhook_default),
            "log_enabled": settings.discord_log_enabled,
            "periodic_log_enabled": settings.discord_periodic_log_enabled,
            "log_level": settings.discord_log_level,
            "log_webhook_configured": bool(settings.discord_log_webhook),
            "startup_test": settings.discord_startup_test,
        },
        "state_db": settings.state_db,
        "state_backend": "postgres" if settings.database_url else "sqlite",
        "log_level": settings.log_level,
        "manual_scan_enabled": bool(settings.scan_token),
    }


def resolved_discord_log_webhook(settings: Settings) -> str:
    """Choose one diagnostic Discord destination without exposing it."""
    return (
        settings.discord_log_webhook
        or settings.discord_webhook_default
        or settings.discord_webhook_btc
        or settings.discord_webhook_eth
    )
