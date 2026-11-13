"""Startup wiring for the L5 trade alert adapter.

Connects ``ControlLogWriter`` to Postgres and ``DiscordRouter`` to Discord.
Database connections are opened per call and never at construction time.
Webhook URLs are never included in exceptions or logs.
"""
from __future__ import annotations

import os

import requests

from .alert_v2 import DiscordRouter
from .error_log import ControlLogWriter
from .l5_trade import L5TradeAlerts


class PsycopgControlDb:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def execute(self, sql, params) -> None:
        import psycopg

        with psycopg.connect(self.database_url) as conn:
            conn.execute(sql, params)

    def execute_returning(self, sql, params) -> int:
        import psycopg

        with psycopg.connect(self.database_url) as conn:
            row = conn.execute(sql, params).fetchone()
            return int(row[0])


def discord_post(url: str, content: str) -> None:
    try:
        response = requests.post(url, json={"content": content}, timeout=5)
        response.raise_for_status()
    except Exception:
        # Drop the original exception: its message may contain the webhook URL.
        raise RuntimeError("discord post failed") from None


def build_l5(settings):
    webhooks = {
        "BTC": os.getenv("DISCORD_WEBHOOK_BTC", ""),
        "ETH": os.getenv("DISCORD_WEBHOOK_ETH", ""),
        "ERROR": os.getenv("DISCORD_WEBHOOK_ERROR", ""),
    }
    db_url = settings.database_url
    if not db_url:
        return None
    writer = ControlLogWriter(PsycopgControlDb(db_url))
    router = DiscordRouter(webhooks, discord_post)
    return L5TradeAlerts(writer, router)
