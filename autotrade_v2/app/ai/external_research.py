"""Read-only external futures evidence for Gemini; never called by the executor.

Public Binance USD-M endpoints are optional. Requests carry no API credentials.
The timestamp of every capture is explicit: data fetched after the strategy
signal is NOT rewritten as evidence available at the original signal close.
"""
from __future__ import annotations

import math
from time import time

import requests

from .independent_smc import analyze_structure

_SYMBOLS = {"BTC-USDT": "BTCUSDT", "ETH-USDT": "ETHUSDT"}
_BASE = "https://fapi.binance.com"


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _timestamp(value):
    try:
        value = int(value)
        return value if value > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


class BinanceFuturesPublic:
    """Small, bounded, read-only cross-exchange context; never affects trades."""

    def __init__(self, *, get=None, timeout=3.0):
        self.get = get or requests.get
        self.timeout = timeout

    def _fetch(self, path, params):
        response = self.get(_BASE + path, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def capture(self, symbol, signal_close_ms):
        target = _SYMBOLS.get(str(symbol).upper())
        observed_ms = int(time() * 1000)
        out = {
            "source": "Binance USD-M public REST",
            "signal_close_ms": int(signal_close_ms),
            "observed_ms": observed_ms,
            "observed_after_signal": observed_ms > int(signal_close_ms),
            "status": "UNAVAILABLE",
            "mark": None,
            "open_interest": None,
        }
        if not target:
            return out
        try:
            p = self._fetch("/fapi/v1/premiumIndex", {"symbol": target})
            mark_price = _number(p.get("markPrice")) if isinstance(p, dict) else None
            funding = _number(p.get("lastFundingRate")) if isinstance(p, dict) else None
            if mark_price is not None and mark_price > 0:
                out["mark"] = {
                    "mark_price": mark_price,
                    "index_price": _number(p.get("indexPrice")),
                    "last_funding_rate": funding,
                    "next_funding_ms": _timestamp(p.get("nextFundingTime")),
                    "source_ms": _timestamp(p.get("time")),
                }
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            pass
        try:
            p = self._fetch("/fapi/v1/openInterest", {"symbol": target})
            interest = _number(p.get("openInterest")) if isinstance(p, dict) else None
            if interest is not None and interest >= 0:
                out["open_interest"] = {
                    "contracts": interest,
                    "source_ms": _timestamp(p.get("time")),
                }
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            pass
        if out["mark"] and out["open_interest"]:
            out["status"] = "AVAILABLE"
        elif out["mark"] or out["open_interest"]:
            out["status"] = "PARTIAL"
        return out

    def structure_as_of(self, symbol, signal_close_ms):
        """Independently compute BOS/CHoCH/FVG on Binance closed bars as-of Entry.

        No future n-bars after the signal are used to confirm a swing.
        """
        target = _SYMBOLS.get(str(symbol).upper())
        result = {"source": "Binance USD-M historical 15m/1h/4h",
                  "as_of_ms": int(signal_close_ms), "status": "UNAVAILABLE",
                  "timeframes": {}}
        if not target:
            return result
        for tf in ("15m", "1h", "4h"):
            try:
                data = self._fetch("/fapi/v1/klines", {
                    "symbol": target, "interval": tf,
                    "endTime": int(signal_close_ms), "limit": 120,
                })
                bars = []
                if not isinstance(data, list):
                    continue
                for row in data:
                    if not isinstance(row, list) or len(row) < 7:
                        continue
                    close_ms = _timestamp(row[6])
                    if not close_ms or close_ms > int(signal_close_ms):
                        continue
                    bars.append({
                        "open_ms": row[0], "close_ms": close_ms,
                        "open": row[1], "high": row[2], "low": row[3],
                        "close": row[4], "volume": row[5],
                    })
                structure = analyze_structure(bars, timeframe=tf,
                                              as_of_ms=int(signal_close_ms))
                if structure["status"] == "AVAILABLE":
                    result["timeframes"][tf] = structure
            except (requests.RequestException, ValueError, TypeError, AttributeError):
                continue
        if len(result["timeframes"]) == 3:
            result["status"] = "AVAILABLE"
        elif result["timeframes"]:
            result["status"] = "PARTIAL"
        return result

    def exit_window(self, symbol, signal_close_ms, closed_ms):
        """Historical 1H Binance candles for qualitative post review, not fill proof."""
        target = _SYMBOLS.get(str(symbol).upper())
        start, end = _timestamp(signal_close_ms), _timestamp(closed_ms)
        result = {
            "source": "Binance USD-M public 1h klines",
            "status": "UNAVAILABLE",
            "signal_close_ms": start,
            "closed_ms": end,
        }
        if not target or not start or not end or end <= start:
            return result
        # Bounded to 72 hours; do not present a truncated sample as the full trade.
        if end - start > 72 * 3600 * 1000:
            result["status"] = "OUT_OF_RANGE"
            return result
        try:
            data = self._fetch("/fapi/v1/klines", {
                "symbol": target, "interval": "1h",
                "startTime": start, "endTime": end, "limit": 75,
            })
            rows = [row for row in data if isinstance(row, list) and len(row) > 6
                    and _timestamp(row[6]) and int(row[6]) <= end]
            if not rows:
                return result
            lows = [_number(row[3]) for row in rows]
            highs = [_number(row[2]) for row in rows]
            closes = [_number(row[4]) for row in rows]
            if any(v is None for v in lows + highs + closes):
                return result
            result.update({
                "status": "AVAILABLE", "bar_count": len(rows),
                "lowest": min(lows), "highest": max(highs),
                "last_close": closes[-1],
                "first_bar_open_ms": _timestamp(rows[0][0]),
                "last_bar_close_ms": _timestamp(rows[-1][6]),
                "resolution": "1h; excludes candle closing after verified exit",
                "not_exchange_fill_evidence": True,
            })
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            pass
        return result
