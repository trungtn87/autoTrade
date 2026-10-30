from __future__ import annotations

import os
from dataclasses import dataclass, replace


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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

    candle_keep_15m: int = 3400
    required_15m: int = 3400
    historical_request_limit: int = 1000
    historical_min_interval_ms: int = 1100

    bootstrap_enabled: bool = _bool("BOOTSTRAP_ENABLED", False)
    websocket_enabled: bool = _bool("WEBSOCKET_ENABLED", False)
    bingx_ws_url: str = os.getenv(
        "BINGX_WS_URL",
        "wss://open-api-swap.bingx.com/swap-market",
    )

    execution_enabled: bool = _bool("EXECUTION_ENABLED", False)
    dry_run: bool = _bool("DRY_RUN", True)

    # 50% fixed TP + 50% exchange-native trailing with full SL.
    # BingX owns order lifecycle after exchange confirmation.
    tp_trailing_split_enabled: bool = _bool("TP_TRAILING_SPLIT_ENABLED", False)
    tp_trailing_activation_pct: float = float(os.getenv("TP_TRAILING_ACTIVATION_PCT", "0.005"))
    tp_trailing_callback_rate: float = float(os.getenv("TP_TRAILING_CALLBACK_RATE", "0.005"))
    # Deprecated compatibility flag: forcibly disabled by live entrypoint.
    position_lifecycle_enabled: bool = False
    # Minimal orphan-order cleanup only; no SL resizing or cancelReplace.
    exit_cleanup_enabled: bool = _bool("EXIT_CLEANUP_ENABLED", False)

    # Layer 4 only. Missing/broken Discord must never block trading.
    discord_enabled: bool = _bool("DISCORD_ENABLED", True)
    discord_webhook_btc: str = os.getenv("DISCORD_WEBHOOK_BTC", "")
    discord_webhook_eth: str = os.getenv("DISCORD_WEBHOOK_ETH", "")
    discord_webhook_error: str = os.getenv("DISCORD_WEBHOOK_ERROR", "")
    # Optional Google Apps Script / external relay. When configured, Discord
    # traffic leaves through the relay instead of Render's outbound network.
    discord_relay_url: str = os.getenv("DISCORD_RELAY_URL", "")
    discord_relay_token: str = os.getenv("DISCORD_RELAY_TOKEN", "")

    # Severe Layer-4 alerts. Email is independent from Discord and must never
    # participate in trading decisions or block the execution path.
    email_alerts_enabled: bool = _bool("EMAIL_ALERTS_ENABLED", False)
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "trungtn87@gmail.com")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_starttls: bool = _bool("SMTP_STARTTLS", True)
    email_from: str = os.getenv("EMAIL_FROM", "trungtn87@gmail.com")
    email_to: str = os.getenv("EMAIL_TO", "trungtn87@gmail.com")
    email_test_on_startup: bool = _bool("EMAIL_TEST_ON_STARTUP", False)

    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()


def enable_runtime_trailing(settings: Settings) -> Settings:
    """Configure exchange-native 50/50 exits without bot-owned SL lifecycle.

    The trailing split remains independently configurable on Render.
    POSITION_LIFECYCLE_ENABLED is ignored even if an old environment variable
    is still set; the bot must not cancel/resize BingX orders post-placement.
    """
    return replace(
        settings,
        tp_trailing_split_enabled=_bool("TP_TRAILING_SPLIT_ENABLED", True),
        position_lifecycle_enabled=False,
        exit_cleanup_enabled=_bool("EXIT_CLEANUP_ENABLED",settings.exit_cleanup_enabled),
        tp_trailing_activation_pct=float(
            os.getenv("TP_TRAILING_ACTIVATION_PCT",str(settings.tp_trailing_activation_pct))
        ),
        tp_trailing_callback_rate=float(
            os.getenv("TP_TRAILING_CALLBACK_RATE",str(settings.tp_trailing_callback_rate))
        ),
    )

def validate_settings(settings: Settings) -> None:
    if not 0 < settings.tp_trailing_activation_pct < 1:
        raise ValueError("TP_TRAILING_ACTIVATION_PCT must be in (0, 1)")
    if not 0 < settings.tp_trailing_callback_rate <= 1:
        raise ValueError("TP_TRAILING_CALLBACK_RATE must be in (0, 1]")
    if settings.historical_request_limit != 1000:
        raise ValueError("historical_request_limit is locked to 1000")
    if settings.historical_min_interval_ms < 1000:
        raise ValueError("historical_min_interval_ms must respect BingX 1/s IP limit")
    if settings.candle_keep_15m < settings.required_15m:
        raise ValueError("candle_keep_15m must be >= required_15m")
    if (settings.bootstrap_enabled or settings.websocket_enabled) and not settings.database_url:
        raise ValueError("Supabase DATABASE_URL is required before bootstrap/WebSocket can be enabled")
    if settings.database_url:
        db_host=settings.database_url.lower()
        if "supabase.co" not in db_host and "pooler.supabase.com" not in db_host:
            raise ValueError("V2 production DATABASE_URL must point to Supabase")
    if settings.execution_enabled and not settings.dry_run:
        if not settings.bingx_api_key or not settings.bingx_api_secret:
            raise ValueError("live execution requires BINGX_API_KEY and BINGX_API_SECRET")
