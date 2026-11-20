"""Live Terminal for AutoTrade V3: read-only HTTP app. Mounted at / by unified_app.py.

Public read-only mode (default, DASHBOARD_PUBLIC_READONLY) redacts order and event IDs and collector
details. Private mode needs DASHBOARD_PASSWORD (12+) and APP_SESSION_SECRET (32+). No trading endpoints.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .data import load_snapshot, make_engine
from .legacy_view import to_legacy
from .payload import build_payload

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "at_session"
ATTEMPTS: dict[str, list[float]] = defaultdict(list)

app = FastAPI(title="AutoTrade V3 Live Terminal", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
_engine = None


def _public_readonly() -> bool:
    return os.environ.get("DASHBOARD_PUBLIC_READONLY", "true").strip().lower() in {"1", "true", "yes", "on"}


def _secrets() -> tuple[str, bytes]:
    password = os.environ.get("DASHBOARD_PASSWORD", "")
    key = os.environ.get("APP_SESSION_SECRET", "")
    if len(password) < 12 or len(key) < 32:
        raise RuntimeError("DASHBOARD_PASSWORD (12+) and APP_SESSION_SECRET (32+) required")
    return password, key.encode()


def _session_token() -> str:
    _, key = _secrets()
    ts = str(int(time.time()))
    nonce = secrets.token_hex(12)
    msg = f"{ts}:{nonce}"
    sig = hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{msg}:{sig}".encode()).decode()


def _authenticated(request: Request) -> bool:
    raw = request.cookies.get(COOKIE, "")
    if not raw:
        return False
    try:
        ts, nonce, sig = base64.urlsafe_b64decode(raw.encode()).decode().split(":", 2)
        if int(time.time()) - int(ts) > 86400 or int(ts) > int(time.time()) + 60:
            return False
        _, key = _secrets()
        expected = hmac.new(key, f"{ts}:{nonce}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except (ValueError, UnicodeError, RuntimeError):
        return False


def _require_auth(request: Request) -> None:
    if not _public_readonly() and not _authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")


def _no_cache(response):
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _get_engine():
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


@app.get("/health")
def health():
    # Configuration flag only: no credentials, exchange data, or order APIs.
    return {"ok": True, "service": "autotrade-dashboard", "engine": "autotrade-v3", "trade_api": False}


@app.get("/")
def home(request: Request):
    return RedirectResponse("/mobile" if _public_readonly() or _authenticated(request) else "/login", status_code=303)


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Scope must cover /mobile, so the worker is served from the root, not /static/.
    return _no_cache(FileResponse(STATIC / "sw.js", media_type="application/javascript",
                                  headers={"Service-Worker-Allowed": "/"}))


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if _public_readonly() or _authenticated(request):
        return RedirectResponse("/mobile", status_code=303)
    return _no_cache(HTMLResponse((STATIC / "login.html").read_text("utf-8")))


@app.post("/login")
def login(request: Request, password: str = Form("")):
    if _public_readonly():
        return RedirectResponse("/mobile", status_code=303)
    expected, _ = _secrets()
    identity = request.client.host if request.client else "unknown"
    now = time.monotonic()
    ATTEMPTS[identity] = [x for x in ATTEMPTS[identity] if now - x < 900]
    if len(ATTEMPTS[identity]) >= 8:
        raise HTTPException(status_code=429, detail="Too many attempts")
    if not hmac.compare_digest(password, expected):
        ATTEMPTS[identity].append(now)
        return _no_cache(HTMLResponse('Sai mật khẩu. <a href="/login">Thử lại</a>', status_code=401))
    ATTEMPTS.pop(identity, None)
    response = RedirectResponse("/mobile", status_code=303)
    response.set_cookie(COOKIE, _session_token(), max_age=86400, httponly=True,
                        secure=os.getenv("COOKIE_SECURE", "true").lower() != "false", samesite="lax", path="/")
    return _no_cache(response)


@app.post("/logout")
def logout():
    response = RedirectResponse("/mobile" if _public_readonly() else "/login", status_code=303)
    response.delete_cookie(COOKIE, path="/")
    return _no_cache(response)


@app.get("/mobile")
def mobile(request: Request):
    if not _public_readonly() and not _authenticated(request):
        return RedirectResponse("/login", status_code=303)
    html = (STATIC / "index.html").read_text("utf-8")
    if _public_readonly():
        html = html.replace("<!--LOGOUT-->", '<span class="quiet" title="Dữ liệu công khai, chỉ đọc">Chỉ xem</span>')
    else:
        html = html.replace("<!--LOGOUT-->", '<form method="post" action="/logout"><button class="quiet">Đăng xuất</button></form>')
    return _no_cache(HTMLResponse(html))


@app.get("/api/dashboard")
def api_dashboard_v2_shape(request: Request, days: str = "all"):
    """Same response shape as the V2 Dashboard API, so the existing UI keeps working on V3 data."""
    _require_auth(request)
    try:
        window_days = None if days.strip().lower() == "all" else max(1, min(int(days), 3650))
    except ValueError:
        return _no_cache(JSONResponse({"error": "days phải là số hoặc 'all'"}, status_code=400))
    try:
        snapshot = load_snapshot(_get_engine())
        full = build_payload(orders=snapshot["orders"], results=snapshot["results"],
                             finances=snapshot["finances"], signals=snapshot["signals"],
                             errors=snapshot["errors"], candle_last_close=snapshot["candle_last_close"],
                             now_ms=snapshot["now_ms"], days=window_days, public=False)
        return _no_cache(JSONResponse(to_legacy(full, snapshot["candle_last_close"])))
    except Exception:
        return _no_cache(JSONResponse({"error": "Không đọc được dữ liệu. Kiểm tra engine/database."}, status_code=503))


@app.get("/api/v3/dashboard")
def api_dashboard(request: Request, symbol: str = "", combo: str = "", days: str = "30"):
    _require_auth(request)
    try:
        window_days = None if days.strip().lower() == "all" else max(1, min(int(days), 3650))
    except ValueError:
        return _no_cache(JSONResponse({"error": "days phải là số hoặc 'all'"}, status_code=400))
    try:
        snapshot = load_snapshot(_get_engine())
        data = build_payload(orders=snapshot["orders"], results=snapshot["results"],
                             finances=snapshot["finances"], signals=snapshot["signals"],
                             errors=snapshot["errors"], candle_last_close=snapshot["candle_last_close"],
                             now_ms=snapshot["now_ms"], symbol=symbol, combo=combo, days=window_days,
                             public=_public_readonly())
        return _no_cache(JSONResponse(data))
    except Exception:
        # Never leak database URLs, credentials or raw rows in error responses.
        return _no_cache(JSONResponse({"error": "Không đọc được dữ liệu. Kiểm tra engine/database."}, status_code=503))
