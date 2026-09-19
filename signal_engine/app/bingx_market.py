from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import pandas as pd

log = logging.getLogger(__name__)


class BingXApiError(RuntimeError):
    def __init__(self, code, message, path: str, retry_at_ms: int | None = None, request_params: dict | None = None):
        safe = request_params or {}
        super().__init__(f"BingX error {code}: {message} | endpoint={path} | params={safe}")
        self.code = code
        self.message = message
        self.path = path
        self.retry_at_ms = retry_at_ms
        self.request_params = request_params or {}


@dataclass
class BingXMarketClient:
    base_url: str = "https://open-api.bingx.com"
    api_key: str = ""
    api_secret: str = ""
    timeout: float = 15.0
    min_interval_sec: float = 1.10
    recv_window_ms: int = 5000

    def __post_init__(self):
        self._last_call = 0.0
        self._blocked_until_ms = 0
        self._client = httpx.Client(timeout=self.timeout, headers={
            "User-Agent": "combo-smc-engine/1.1",
            "X-SOURCE-KEY": "BX-AI-SKILL",
        })

    def _throttle(self):
        now_ms = int(time.time() * 1000)
        if now_ms < self._blocked_until_ms:
            remain = int((self._blocked_until_ms - now_ms + 999) / 1000)
            raise RuntimeError(f"BingX circuit breaker active; retry after about {remain}s")

        wait = self.min_interval_sec - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)

    def _signed_params(self, params: dict) -> dict:
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "BINGX_API_KEY/BINGX_API_SECRET are required by the current BingX swap market API"
            )
        all_params = dict(params)
        all_params["timestamp"] = int(time.time() * 1000)
        all_params.setdefault("recvWindow", self.recv_window_ms)

        # BingX signs the raw sorted k=v query string.
        qs = "&".join(f"{k}={all_params[k]}" for k in sorted(all_params))
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            qs.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        all_params["signature"] = signature
        return all_params

    @staticmethod
    def _retry_at_from_message(message: str) -> int | None:
        m = re.search(r"retry after time:\s*(\d+)", message or "", flags=re.I)
        return int(m.group(1)) if m else None

    def _get(self, path: str, params: dict) -> dict:
        self._throttle()
        safe_params = {k: v for k, v in params.items() if k not in {"signature"}}
        started = time.monotonic()
        log.info("BINGX_REQ path=%s params=%s", path, safe_params)
        signed = self._signed_params(params)
        headers = {
            "X-BX-APIKEY": self.api_key,
            "X-SOURCE-KEY": "BX-AI-SKILL",
        }

        r = self._client.get(self.base_url + path, params=signed, headers=headers)
        self._last_call = time.monotonic()
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        log.info("BINGX_HTTP path=%s status=%s elapsed_ms=%s", path, r.status_code, elapsed_ms)
        r.raise_for_status()

        payload = r.json()
        code = payload.get("code")
        if code != 0:
            msg = str(payload.get("msg", ""))
            retry_at_ms = self._retry_at_from_message(msg)
            if str(code) == "109429":
                # Stop hammering the endpoint until BingX says it is safe again.
                self._blocked_until_ms = retry_at_ms or (int(time.time() * 1000) + 15 * 60 * 1000)
            elif str(code) in {"109415", "109425"}:
                # One invalid/paused/unsupported market-data response is enough.
                # Repeating it can trigger BingX 109429 for the whole quote API.
                self._blocked_until_ms = int(time.time() * 1000) + 15 * 60 * 1000
            log.error(
                "BINGX_API_ERROR code=%s path=%s params=%s retry_at_ms=%s msg=%s",
                code, path, safe_params, retry_at_ms, msg,
            )
            raise BingXApiError(code, msg, path, retry_at_ms, safe_params)

        log.info("BINGX_OK path=%s code=0 elapsed_ms=%s", path, elapsed_ms)
        return payload

    def contracts(self) -> dict:
        """Return raw BingX perpetual contract metadata."""
        payload = self._get("/openApi/swap/v2/quote/contracts", {})
        return payload.get("data") or {}

    def contract_symbols(self) -> set[str]:
        data = self.contracts()
        items = data if isinstance(data, list) else data.get("contracts", []) if isinstance(data, dict) else []
        out = set()
        for item in items:
            if isinstance(item, dict) and item.get("symbol"):
                out.add(str(item["symbol"]).upper())
        return out

    def server_time_ms(self) -> int:
        payload = self._get("/openApi/swap/v2/server/time", {})
        data = payload.get("data", {})
        return int(data.get("serverTime"))

    def klines(self, symbol: str, interval: str, limit: int, start_time: int | None = None, end_time: int | None = None) -> pd.DataFrame:
        allowed = {"1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w","1M"}
        if interval not in allowed:
            raise ValueError(f"Unsupported BingX interval: {interval}")
        if not re.fullmatch(r"[A-Z0-9]+-[A-Z]+", symbol):
            raise ValueError(f"Invalid BingX symbol format: {symbol}")
        if limit <= 0 or limit > 1440:
            raise ValueError("BingX kline limit must be 1..1440")

        params = {"symbol": symbol, "interval": interval, "limit": int(limit)}
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)

        payload = self._get(
            "/openApi/swap/v3/quote/klines",
            params,
        )

        data = payload.get("data") or []
        if isinstance(data, dict):
            # Be tolerant of wrapper-style responses used by some BingX variants.
            data = data.get("data") or data.get("list") or data.get("rows") or []

        interval_ms = {
            "1m": 60_000,
            "3m": 180_000,
            "5m": 300_000,
            "15m": 900_000,
            "30m": 1_800_000,
            "1h": 3_600_000,
            "2h": 7_200_000,
            "4h": 14_400_000,
            "6h": 21_600_000,
            "8h": 28_800_000,
            "12h": 43_200_000,
            "1d": 86_400_000,
            "3d": 259_200_000,
            "1w": 604_800_000,
            "1M": 2_592_000_000,
        }[interval]

        cols = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
        rows = []
        for row in data:
            if isinstance(row, dict):
                open_time = row.get("openTime", row.get("time"))
                close_time = row.get("closeTime")
                if open_time is None:
                    continue
                if close_time is None:
                    close_time = int(open_time) + interval_ms - 1
                values = [
                    open_time,
                    row.get("open"),
                    row.get("high"),
                    row.get("low"),
                    row.get("close"),
                    row.get("volume"),
                    close_time,
                ]
                rows.append(values)
                continue

            if isinstance(row, (list, tuple)) and len(row) >= 7:
                rows.append(list(row[:7]))

        df = pd.DataFrame(rows, columns=cols)
        if df.empty:
            return df

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in ["open_time", "close_time"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["open_time", "close_time", "open", "high", "low", "close", "volume"])
        df["open_time"] = df["open_time"].astype("int64")
        df["close_time"] = df["close_time"].astype("int64")
        return df.sort_values("open_time").drop_duplicates("open_time", keep="last").reset_index(drop=True)


def closed_only(df: pd.DataFrame, now_ms: int, safety_ms: int = 1500) -> pd.DataFrame:
    if df.empty:
        return df
    cutoff = now_ms - safety_ms
    return df[df["close_time"] <= cutoff].copy().reset_index(drop=True)


def aggregate_1h_to_6h(df: pd.DataFrame) -> pd.DataFrame:
    """Build fully closed UTC-aligned 6H candles from closed 1H candles.

    Only complete groups of six consecutive 1H candles are returned. This
    avoids relying on BingX's 6h Kline request while preserving a true 360m
    OHLCV series for Combo 4.
    """
    if df.empty:
        return df.copy()

    one_hour_ms = 3_600_000
    six_hour_ms = 6 * one_hour_ms

    x = df.sort_values("open_time").drop_duplicates("open_time", keep="last").copy()
    x["bucket"] = (x["open_time"] // six_hour_ms) * six_hour_ms

    rows = []
    for bucket, g in x.groupby("bucket", sort=True):
        g = g.sort_values("open_time")
        if len(g) != 6:
            continue

        expected = [int(bucket) + i * one_hour_ms for i in range(6)]
        actual = [int(v) for v in g["open_time"].tolist()]
        if actual != expected:
            continue

        rows.append({
            "open_time": int(bucket),
            "open": float(g.iloc[0]["open"]),
            "high": float(g["high"].max()),
            "low": float(g["low"].min()),
            "close": float(g.iloc[-1]["close"]),
            "volume": float(g["volume"].sum()),
            "close_time": int(bucket) + six_hour_ms - 1,
        })

    return pd.DataFrame(
        rows,
        columns=["open_time", "open", "high", "low", "close", "volume", "close_time"],
    )
