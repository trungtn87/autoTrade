from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import pandas as pd


@dataclass
class BingXMarketClient:
    base_url: str = "https://open-api.bingx.com"
    timeout: float = 15.0
    min_interval_sec: float = 1.05

    def __post_init__(self):
        self._last_call = 0.0
        self._client = httpx.Client(timeout=self.timeout, headers={"User-Agent": "combo-smc-engine/1.0"})

    def _throttle(self):
        wait = self.min_interval_sec - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)

    def _get(self, path: str, params: dict) -> dict:
        self._throttle()
        params = dict(params)
        params.setdefault("timestamp", int(time.time() * 1000))
        r = self._client.get(self.base_url + path, params=params)
        self._last_call = time.monotonic()
        r.raise_for_status()
        payload = r.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"BingX error {payload.get('code')}: {payload.get('msg')}")
        return payload

    def server_time_ms(self) -> int:
        payload = self._get("/openApi/swap/v2/server/time", {})
        data = payload.get("data", {})
        return int(data.get("serverTime"))

    def klines(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        if limit > 1440:
            raise ValueError("BingX max kline limit is 1440 per request")
        payload = self._get(
            "/openApi/swap/v3/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": int(limit)},
        )
        data = payload.get("data") or []
        cols = [
            "open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "trades", "taker_buy_base", "taker_buy_quote",
        ]
        rows = []
        for row in data:
            if len(row) < 7:
                continue
            row = list(row) + [None] * max(0, len(cols) - len(row))
            rows.append(row[:len(cols)])
        df = pd.DataFrame(rows, columns=cols)
        if df.empty:
            return df
        for col in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in ["open_time", "close_time", "trades"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open_time", "close_time", "open", "high", "low", "close", "volume"])
        df["open_time"] = df["open_time"].astype("int64")
        df["close_time"] = df["close_time"].astype("int64")
        df = df.sort_values("open_time").drop_duplicates("open_time", keep="last").reset_index(drop=True)
        return df


def closed_only(df: pd.DataFrame, now_ms: int, safety_ms: int = 1500) -> pd.DataFrame:
    if df.empty:
        return df
    cutoff = now_ms - safety_ms
    return df[df["close_time"] <= cutoff].copy().reset_index(drop=True)
