"""Owner-only runtime diagnostics for AI Analyst; no access to trading controls.

Only explicitly initiated tests make Gemini/Discord outbound requests.
Reports intentionally exclude API keys, database URLs and webhook URLs/bodies.
"""
from __future__ import annotations

import os
import re
import threading
import time
from urllib.parse import urlsplit

import psycopg
import requests

from .gemini_config import generation_config

DIAGNOSTIC_TIMEOUT_SECONDS = 25

_PROBE_LOCK = threading.Lock()
_NEXT_PROBE: dict[str, float] = {}


def _enabled(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "yes", "true", "on"}


def _model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()


def _http_hint(status: int, provider: str) -> tuple[str, str]:
    if provider == "gemini":
        return {
            400: ("BAD_REQUEST", "Gemini từ chối nội dung/yêu cầu; kiểm tra phiên bản API và model."),
            401: ("AUTH_FAILED", "API key Gemini không được chấp nhận."),
            403: ("PERMISSION_DENIED", "API key hoặc project Google chưa có quyền sử dụng model."),
            404: ("MODEL_NOT_FOUND", "Model không tồn tại hoặc không khả dụng cho API key. Kiểm tra GEMINI_MODEL."),
            429: ("RATE_LIMIT", "Gemini hết quota hoặc bị giới hạn tần suất. Kiểm tra AI Studio."),
            500: ("PROVIDER_ERROR", "Google Gemini đang lỗi phía dịch vụ."),
            503: ("PROVIDER_UNAVAILABLE", "Google Gemini tạm thời không khả dụng."),
            504: ("PROVIDER_TIMEOUT", "Google Gemini xử lý quá hạn."),
        }.get(status, ("HTTP_ERROR", "Gemini trả lỗi HTTP. Kiểm tra quyền/quota của API key."))
    return {
        400: ("BAD_REQUEST", "Discord từ chối nội dung thử nghiệm."),
        401: ("AUTH_FAILED", "Webhook Discord không được chấp nhận."),
        403: ("FORBIDDEN", "Discord/Cloudflare từ chối yêu cầu gửi."),
        404: ("WEBHOOK_NOT_FOUND", "Webhook đã bị xóa hoặc đường dẫn không còn hợp lệ."),
        429: ("RATE_LIMIT", "Discord giới hạn tần suất hoặc Cloudflare chặn tạm thời."),
        500: ("PROVIDER_ERROR", "Discord đang lỗi phía dịch vụ."),
        502: ("PROVIDER_ERROR", "Discord hoặc proxy đang lỗi."),
        503: ("PROVIDER_UNAVAILABLE", "Discord tạm thời không khả dụng."),
    }.get(status, ("HTTP_ERROR", "Discord trả lỗi HTTP. Kiểm tra webhook hoặc mạng VPS."))


def diagnose_database(db_factory) -> dict:
    if not os.getenv("DATABASE_URL"):
        return {"status": "error", "code": "MISSING_DATABASE_URL",
                "detail": "Thiếu DATABASE_URL trong environment container."}
    try:
        with db_factory() as conn:
            conn.execute("SET LOCAL statement_timeout = '4000ms'")
            tables = conn.execute("""
                SELECT to_regclass('autotrade_analytics.ai_reviews') IS NOT NULL AS reviews,
                       to_regclass('autotrade_analytics.ai_chat_sessions') IS NOT NULL AS sessions,
                       to_regclass('autotrade_analytics.ai_chat_messages') IS NOT NULL AS messages,
                       to_regclass('autotrade_analytics.trade_results') IS NOT NULL AS trade_results
            """).fetchone()
            missing = [name for name in ("reviews", "sessions", "messages") if not tables[name]]
            if missing:
                return {"status": "error", "code": "AI_SCHEMA_MISSING",
                        "detail": "Thiếu bảng AI: " + ", ".join(missing) +
                                  ". Cần kiểm tra migration trên Supabase.",
                        "post_trade_ready": bool(tables["trade_results"])}
            row = conn.execute("""
                SELECT (SELECT count(*) FROM autotrade_analytics.ai_reviews) AS reviews,
                       (SELECT count(*) FROM autotrade_analytics.ai_chat_messages) AS chat_messages,
                       (SELECT count(*) FROM autotrade_analytics.ai_post_reviews) AS post_reviews
            """).fetchone()
            return {"status": "ok", "code": "DB_OK",
                    "detail": "Supabase kết nối và truy vấn các bảng AI thành công.",
                    "reviews": int(row["reviews"]),
                    "chat_messages": int(row["chat_messages"]),
                    "post_reviews": int(row["post_reviews"]),
                    "post_trade_ready": bool(tables["trade_results"])}
    except psycopg.Error as exc:
        sqlstate = getattr(exc, "sqlstate", None) or ""
        if sqlstate == "28P01":
            code, hint = "DB_AUTH", "Sai tài khoản hoặc mật khẩu PostgreSQL trong DATABASE_URL."
        elif sqlstate in ("42501",):
            code, hint = "DB_PERMISSION", "Tài khoản DB thiếu quyền đọc bảng AI."
        elif sqlstate in ("42P01", "3F000"):
            code, hint = "DB_SCHEMA", "Thiếu bảng hoặc schema AI; kiểm tra migration."
        elif sqlstate == "57014":
            code, hint = "DB_TIMEOUT", "Truy vấn Supabase quá hạn."
        elif sqlstate.startswith("08"):
            code, hint = "DB_CONNECTION", "Không kết nối được tới PostgreSQL từ container."
        else:
            code, hint = "DB_ERROR", "Truy vấn PostgreSQL thất bại; kiểm tra DATABASE_URL, quyền và schema."
        return {"status": "error", "code": code, "sqlstate": sqlstate[:5] or None,
                "detail": hint}
    except Exception:
        # Never reflect psycopg URL, host name, password, or raw exception.
        return {"status": "error", "code": "DB_CONNECTION",
                "detail": "Không mở được kết nối Supabase từ container."}


def summary(db_factory) -> dict:
    gemini_key = bool(os.getenv("GEMINI_API_KEY", "").strip())
    observer_on = _enabled("AI_OBSERVE_ENABLED", "false")
    discord_on = _enabled("DISCORD_ENABLED", "true")
    hooks = {s: bool(os.getenv("DISCORD_WEBHOOK_" + s, "").strip()) for s in ("BTC", "ETH")}
    return {
        "database": diagnose_database(db_factory),
        "gemini": {
            "status": "configured" if gemini_key else "error",
            "code": "CONFIGURED_NOT_TESTED" if gemini_key else "MISSING_API_KEY",
            "model": _model(),
            "observer_enabled": observer_on,
            "detail": (
                "API key đã cấu hình, chưa thử gọi Gemini."
                if gemini_key else "Thiếu GEMINI_API_KEY trong container."
            ) + (" Gemini OBSERVE đang bật." if observer_on
                 else " Gemini OBSERVE đang tắt (AI_OBSERVE_ENABLED)."),
        },
        "discord": {
            s: {
                "status": ("configured" if discord_on and hooks[s] else "error"),
                "code": ("CONFIGURED_NOT_TESTED" if discord_on and hooks[s]
                         else "DISCORD_DISABLED" if not discord_on else "MISSING_WEBHOOK"),
                "detail": ("Webhook đã cấu hình, chưa gửi thử." if discord_on and hooks[s]
                           else "DISCORD_ENABLED đang tắt." if not discord_on
                           else "Thiếu DISCORD_WEBHOOK_" + s + " trong container.")
            }
            for s in ("BTC", "ETH")
        },
        "observe_only": True,
    }


def _take_probe_slot(name: str, seconds: float) -> int:
    now = time.monotonic()
    with _PROBE_LOCK:
        remaining = max(0.0, _NEXT_PROBE.get(name, 0) - now)
        if remaining:
            return max(1, int(remaining + 0.99))
        _NEXT_PROBE[name] = now + seconds
    return 0


def probe_gemini() -> dict:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    model = _model()
    if not key:
        return {"status": "error", "code": "MISSING_API_KEY",
                "detail": "GEMINI_API_KEY chưa được cấu hình."}
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", model):
        return {"status": "error", "code": "INVALID_MODEL",
                "detail": "GEMINI_MODEL chứa ký tự không hợp lệ."}
    cooldown = _take_probe_slot("gemini", 30)
    if cooldown:
        return {"status": "warning", "code": "COOLDOWN",
                "detail": f"Đã chạy thử Gemini gần đây. Thử lại sau {cooldown} giây."}
    started = time.monotonic()
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": "Return exactly OK."}]}],
                  "generationConfig": generation_config(model, 256)},
            timeout=DIAGNOSTIC_TIMEOUT_SECONDS,
        )
        elapsed = int((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            code, detail = _http_hint(response.status_code, "gemini")
            return {"status": "error", "code": code, "http_status": response.status_code,
                    "latency_ms": elapsed, "model": model, "detail": detail}
        try:
            body = response.json()
            parts = ((body.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            has_text = any(isinstance(p, dict) and p.get("text", "").strip() for p in parts)
        except (ValueError, TypeError, AttributeError, IndexError):
            has_text = False
        return {"status": "ok" if has_text else "warning",
                "code": "GENERATE_OK" if has_text else "EMPTY_RESPONSE",
                "http_status": 200, "latency_ms": elapsed, "model": model,
                "detail": ("Gemini đã sinh phản hồi thử nghiệm thành công."
                           if has_text else "Gemini trả HTTP 200 nhưng không có nội dung trả lời.")}
    except requests.ConnectTimeout:
        return {"status": "error", "code": "CONNECT_TIMEOUT", "model": model,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "detail": "Container không kết nối được tới máy chủ Gemini trong thời hạn cho phép."}
    except requests.Timeout:
        return {"status": "error", "code": "TIMEOUT", "model": model,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "detail": f"Gemini chưa trả lời trong {DIAGNOSTIC_TIMEOUT_SECONDS} giây; có thể phản hồi chậm hoặc mạng không ổn định."}
    except requests.RequestException:
        return {"status": "error", "code": "NETWORK_ERROR", "model": model,
                "detail": "Container không gọi được API Gemini (DNS/TLS/mạng)."}
    except Exception:
        return {"status": "error", "code": "PROBE_ERROR", "model": model,
                "detail": "Không thể hoàn tất bài thử Gemini."}


def _valid_discord_webhook(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname in {"discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"}
            and parsed.port in (None, 443)
            and bool(re.fullmatch(r"/api(?:/v[0-9]+)?/webhooks/[0-9]+/[A-Za-z0-9_.-]+", parsed.path))
            and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment
        )
    except ValueError:
        return False


def probe_discord(market: str) -> dict:
    if market not in ("BTC", "ETH"):
        return {"status": "error", "code": "INVALID_MARKET", "detail": "Chỉ hỗ trợ BTC và ETH."}
    if not _enabled("DISCORD_ENABLED", "true"):
        return {"status": "error", "code": "DISCORD_DISABLED",
                "detail": "DISCORD_ENABLED đang tắt."}
    url = os.getenv("DISCORD_WEBHOOK_" + market, "").strip()
    if not url:
        return {"status": "error", "code": "MISSING_WEBHOOK",
                "detail": "Thiếu DISCORD_WEBHOOK_" + market + "."}
    if not _valid_discord_webhook(url):
        return {"status": "error", "code": "INVALID_WEBHOOK_URL",
                "detail": "Webhook không phải URL Discord HTTPS hợp lệ."}
    cooldown = _take_probe_slot("discord_" + market, 90)
    if cooldown:
        return {"status": "warning", "code": "COOLDOWN",
                "detail": f"Vừa gửi thử Discord {market}. Thử lại sau {cooldown} giây."}
    try:
        started = time.monotonic()
        res = requests.post(
            url,
            json={"content": f"🧪 **AutoTrade AI Analyst · KIỂM TRA {market}**\n"
                             "Tin nhắn thử webhook từ trang /ai. Không phải tín hiệu giao dịch.",
                  "allowed_mentions": {"parse": []}},
            headers={"User-Agent": "DiscordBot (https://github.com/trungtn87/autoTrade, 2.0)",
                     "Accept": "application/json"},
            timeout=8,
        )
        elapsed = int((time.monotonic() - started) * 1000)
        if res.status_code in (200, 204):
            return {"status": "ok", "code": "DISCORD_DELIVERED", "http_status": res.status_code,
                    "latency_ms": elapsed, "detail": f"Discord {market} chấp nhận tin nhắn thử."}
        code, hint = _http_hint(res.status_code, "discord")
        return {"status": "error", "code": code, "http_status": res.status_code,
                "latency_ms": elapsed, "detail": hint}
    except requests.Timeout:
        return {"status": "error", "code": "TIMEOUT",
                "detail": "Discord không phản hồi trong 8 giây."}
    except requests.RequestException:
        return {"status": "error", "code": "NETWORK_ERROR",
                "detail": "Container không kết nối được Discord."}
    except Exception:
        return {"status": "error", "code": "PROBE_ERROR",
                "detail": "Không thể gửi tin nhắn thử tới Discord."}
