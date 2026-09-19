from __future__ import annotations

import hashlib
import hmac
import re
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import pandas as pd


class BingXApiError(RuntimeError):
    def __init__(self, code, message, path: str, retry_at_ms: int | None = None):
        super().__init__(f"BingX error {code}: {message} | endpoint={path}")
        self.code = code
        self.message = message
        self.path = path
        self.retry_at_ms = retry_at_ms


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
        signed = self._signed_params(params)
        headers = {
            "X-BX-APIKEY": self.api_key,
            "X-SOURCE-KEY": "BX-AI-SKILL",
        }

        r = self._client.get(self.base_url + path, params=signed, headers=headers)
        self._last_call = time.monotonic()
        r.raise_for_status()

        payload = r.json()
        code = payload.get("code")
        if code != 0:
            msg = str(payload.get("msg", ""))
            retry_at_ms = self._retry_at_from_message(msg)
            if str(code) == "109429":
                # Stop hammering the endpoint until BingX says it is safe again.
                self._blocked_until_ms = retry_at_ms or (int(time.time() * 1000) + 15 * 60 * 1000)
            raise BingXApiError(code, msg, path, retry_at_ms)

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

    def klines(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        allowed = {"1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w","1M"}
        if interval not in allowed:
            raise ValueError(f"Unsupported BingX interval: {interval}")
        if not re.fullmatch(r"[A-Z0-9]+-[A-Z]+", symbol):
            raise ValueError(f"Invalid BingX symbol format: {symbol}")
        if limit <= 0 or limit > 1000:
            raise ValueError("BingX kline limit must be 1..1000")

        payload = self._get(
            "/openApi/swap/v3/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": int(limit)},
        )

        data = payload.get("data") or []
        cols = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
        rows = []
        for row in data:
            if len(row) < 7:
                continue
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
