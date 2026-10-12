"""Durable, per-candle trade notification batches for Discord.

Only final execution results enter this outbox. Never delays or changes BingX orders.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone

import psycopg
import requests

log = logging.getLogger(__name__)

QUIET_SECONDS = 25
MAX_WAIT_SECONDS = 100
MIN_SEND_SPACING_SECONDS = 2.5
MAX_MESSAGE_LENGTH = 1900


def _number(value) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.8f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _combo(value) -> str:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return str(value or "-")
    if 101 <= num <= 106:
        return f"C{num - 90}"
    return "TIER" if num == 11 else f"C{num}"


def _batch_key(event) -> str:
    """A 1h and 15m result closing on the same candle boundary share a batch."""
    parts = str(event.event_id or "").split("|")
    if len(parts) >= 5 and parts[-1].isdigit():
        return f"{event.symbol}|{parts[-1]}"
    try:
        timestamp = datetime.fromisoformat(event.created_at).timestamp()
    except (ValueError, TypeError):
        timestamp = time.time()
    return f"{event.symbol}|fallback-{int(timestamp // 900)}"


def _row_text(data: dict) -> str:
    key = data.get("event_key", "")
    successful = key == "L3.EXEC.ORDER_COMPLETE"
    label = "✅ Đã khớp, TP/SL xác nhận" if successful else f"⚠️ {key.split('.')[-1]}"
    prefix = f"{label} | {_combo(data.get('combo'))} {data.get('side') or '-'}"
    if data.get("timeframe"):
        prefix += f" ({data['timeframe']})"
    lines = [
        prefix,
        f"Entry: {_number(data.get('entry'))} | TP: {_number(data.get('tp'))} | SL: {_number(data.get('sl'))}",
    ]
    if data.get("order_id"):
        lines.append(f"Order: {str(data['order_id'])[:90]}")
    if not successful:
        lines.append(f"Lý do: {str(data.get('error') or '-').replace(chr(10), ' ')[:160]}")
    return "\n".join(lines)


def _one_message(symbol: str, rows: list[tuple[int, dict]]) -> tuple[str, list[int]]:
    header = f"📦 {symbol} — tổng hợp lệnh đã xử lý\n"
    chosen: list[int] = []
    parts: list[str] = []
    used = len(header)
    for row_id, data in rows:
        block = _row_text(data)
        addition = len(block) + (2 if parts else 0)
        if used + addition > MAX_MESSAGE_LENGTH and parts:
            break
        # A pathological single error string must not prevent the entire batch.
        if used + addition > MAX_MESSAGE_LENGTH:
            block = block[:MAX_MESSAGE_LENGTH - used - 1]
        parts.append(block)
        chosen.append(row_id)
        used += len(block) + (2 if len(parts) > 1 else 0)
    return header + "\n\n".join(parts), chosen


def _retry_seconds(response, attempt: int) -> float:
    value = response.headers.get("Retry-After")
    if not value:
        try:
            value = response.json().get("retry_after")
        except (ValueError, AttributeError):
            pass
    try:
        if value is not None:
            return min(172800.0, max(2.0, float(value) + 1.0))
    except (ValueError, TypeError):
        pass
    return min(900.0, 30.0 * 2 ** min(attempt, 5))


class DiscordTradeOutbox:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.last_send_at = 0.0

    def stage(self, event) -> None:
        """Durably save a final trade outcome before considering network delivery."""
        details = event.details or {}
        parts = str(event.event_id or "").split("|")
        payload = {
            "event_key": event.event_key,
            "combo": event.combo,
            "side": details.get("side"),
            "entry": details.get("entry"),
            "tp": details.get("tp"),
            "sl": details.get("sl"),
            "order_id": event.order_id or details.get("order_id"),
            "error": details.get("error") or event.message,
            "timeframe": parts[1] if len(parts) >= 5 else "",
        }
        event_id = event.event_id or hashlib.sha256(
            f"{event.event_key}|{event.symbol}|{event.order_id}|{event.created_at}".encode()
        ).hexdigest()
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.discord_trade_outbox
                        (event_key,event_id,symbol,batch_key,payload,next_attempt_at)
                    VALUES (%s,%s,%s,%s,%s::jsonb,
                        GREATEST(now(),COALESCE(
                            (SELECT MAX(next_attempt_at)
                               FROM public.discord_trade_outbox
                              WHERE sent_at IS NULL), now())))
                    ON CONFLICT (event_key,event_id) DO NOTHING
                    """,
                    (event.event_key, event_id, event.symbol, _batch_key(event),
                     json.dumps(payload, ensure_ascii=False, default=str)),
                )

    def flush_one(self, webhooks: dict[str, str], headers: dict[str, str]) -> bool:
        """Send at most one batch and one HTTP request; return whether work was attempted."""
        if time.monotonic() - self.last_send_at < MIN_SEND_SPACING_SECONDS:
            return False
        with psycopg.connect(self.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT batch_key,symbol
                      FROM public.discord_trade_outbox
                     WHERE sent_at IS NULL
                     GROUP BY batch_key,symbol
                    HAVING max(received_at) <= now() - (%s * interval '1 second')
                        OR min(received_at) <= now() - (%s * interval '1 second')
                     ORDER BY min(received_at)
                     LIMIT 10
                    """,
                    (QUIET_SECONDS, MAX_WAIT_SECONDS),
                )
                groups = cur.fetchall()
                for batch_key, symbol in groups:
                    url = webhooks.get(symbol)
                    if not url:
                        continue
                    cur.execute(
                        """
                        SELECT id,payload,attempts
                          FROM public.discord_trade_outbox
                         WHERE batch_key=%s AND sent_at IS NULL
                           AND next_attempt_at <= now()
                         ORDER BY received_at,id
                         FOR UPDATE SKIP LOCKED
                        """,
                        (batch_key,),
                    )
                    pending = cur.fetchall()
                    if not pending:
                        continue
                    decoded = [
                        (row_id, payload if isinstance(payload, dict) else json.loads(payload))
                        for row_id, payload, _ in pending
                    ]
                    content, ids = _one_message(symbol, decoded)
                    if not ids:
                        continue
                    self.last_send_at = time.monotonic()
                    try:
                        response = requests.post(
                            url, json={"content": content}, headers=headers, timeout=8,
                        )
                        if response.status_code == 204:
                            cur.execute(
                                """
                                UPDATE public.discord_trade_outbox
                                   SET sent_at=now(),last_error=NULL
                                 WHERE id=ANY(%s)
                                """, (ids,),
                            )
                            log.info(
                                "EVENT key=L4.DISCORD.BATCH_SENT symbol=%s count=%s batch=%s status=204",
                                symbol, len(ids), batch_key,
                            )
                            return True

                        retry = _retry_seconds(response, max(row[2] for row in pending) + 1)
                        message = f"Discord HTTP {response.status_code}"
                        if response.status_code == 429:
                            # A Cloudflare 1015 block may last many hours.
                            # Do not probe it repeatedly with every new order.
                            cur.execute(
                                """
                                UPDATE public.discord_trade_outbox
                                   SET next_attempt_at=GREATEST(
                                       next_attempt_at,
                                       now()+(%s * interval '1 second')),
                                       last_error=%s
                                 WHERE sent_at IS NULL
                                """, (retry, f"{message}; retry_after={retry:.0f}s"),
                            )
                        else:
                            cur.execute(
                                """
                                UPDATE public.discord_trade_outbox
                                   SET attempts=attempts+1,
                                       next_attempt_at=now()+(%s * interval '1 second'),
                                       last_error=%s
                                 WHERE id=ANY(%s)
                                """, (retry, message, ids),
                            )
                        log.warning(
                            "EVENT key=L4.DISCORD.BATCH_DEFERRED symbol=%s count=%s "
                            "http=%s retry_after=%.0fs",
                            symbol, len(ids), response.status_code, retry,
                        )
                        return True
                    except requests.RequestException as exc:
                        delay = 90.0
                        cur.execute(
                            """
                            UPDATE public.discord_trade_outbox
                               SET attempts=attempts+1,
                                   next_attempt_at=now()+(%s * interval '1 second'),
                                   last_error=%s
                             WHERE id=ANY(%s)
                            """, (delay, type(exc).__name__, ids),
                        )
                        log.warning(
                            "EVENT key=L4.DISCORD.BATCH_NETWORK_FAIL symbol=%s "
                            "count=%s error=%s",
                            symbol, len(ids), type(exc).__name__,
                        )
                        return True
        return False
