from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import httpx
import pandas as pd

from ..control.errors import DataLayerError

log = logging.getLogger(__name__)


@dataclass
class PublicMarketClient:
    base_url: str = "https://open-api.bingx.com"
    timeout: float = 15.0

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": "autotrade-v2/1.0", "X-SOURCE-KEY": "BX-AI-SKILL"},
        )

    def klines(self, symbol: str, interval: str = "15m", limit: int = 1000) -> pd.DataFrame:
        if interval != "15m":
            raise ValueError("V2 market layer currently supports only 15m")
        if not re.fullmatch(r"[A-Z0-9]+-[A-Z]+", symbol):
            raise ValueError(f"invalid BingX symbol: {symbol}")
        if not (1 <= int(limit) <= 1000):
            raise ValueError("kline limit must be 1..1000")

        path = "/openApi/swap/v3/quote/klines"
        params = {"symbol": symbol, "interval": interval, "limit": int(limit)}
        started = time.monotonic()
        log.info("DATA_HTTP_REQUEST provider=bingx endpoint=%s symbol=%s interval=%s limit=%s auth=public",
                 path, symbol, interval, limit)

        try:
            response = self._client.get(self.base_url + path, params=params)
        except Exception as exc:
            raise DataLayerError(f"BingX market transport failed: {type(exc).__name__}: {exc}") from exc

        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        code = payload.get("code") if isinstance(payload, dict) else None
        msg = str(payload.get("msg", "")) if isinstance(payload, dict) else ""

        log.info("DATA_HTTP_RESPONSE provider=bingx endpoint=%s status=%s code=%s elapsed_ms=%s",
                 path, response.status_code, code, elapsed_ms)

        if response.status_code != 200 or code not in (0, "0"):
            raise DataLayerError(
                f"BingX market error status={response.status_code} code={code} msg={msg} "
                f"endpoint={path} params={params}"
            )

        data = payload.get("data") or []
        if isinstance(data, dict):
            data = data.get("data") or data.get("list") or data.get("rows") or []

        rows = []
        for row in data:
            if isinstance(row, dict):
                open_time = row.get("openTime", row.get("time"))
                if open_time is None:
                    continue
                close_time = row.get("closeTime")
                if close_time is None:
                    close_time = int(open_time) + 900_000 - 1
                rows.append([
                    open_time, row.get("open"), row.get("high"), row.get("low"),
                    row.get("close"), row.get("volume"), close_time,
                ])
            elif isinstance(row, (list, tuple)) and len(row) >= 7:
                rows.append(list(row[:7]))

        cols = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
        df = pd.DataFrame(rows, columns=cols)
        if df.empty:
            return df

        numeric = {
            c: pd.to_numeric(df[c], errors="coerce")
            for c in cols
        }
        df = pd.DataFrame(numeric).dropna().copy(deep=True)
        df["open_time"] = df["open_time"].astype("int64")
        df["close_time"] = df["close_time"].astype("int64")
        return (
            df.sort_values("open_time")
            .drop_duplicates("open_time", keep="last")
            .reset_index(drop=True)
        )


def closed_only(df: pd.DataFrame, now_ms: int, safety_ms: int = 1500) -> pd.DataFrame:
    if df.empty:
        return df
    return df[df["close_time"] <= now_ms - safety_ms].copy().reset_index(drop=True)
