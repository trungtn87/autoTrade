"""ETH-USDT M15 long history for the ETH C1 H1_BOTH_1 generator only.

Why: the Lab EMA150 4H state converges only on long history. With the live
3400-bar snapshot, 4H EMA values drift enough to flip BUY/SELL decisions
(measured on ETH M15 RUN17: 19 BUY and 2 SELL flips in 119 sampled hours).
With 12000 bars the drift is below 0.002 points on the same samples.

Rules:
- Other combos and V2 keep the 3400-bar snapshot. This cache is ETH C1 only.
- Warm-up uses the existing BingX public REST history (`fetch_history`), paged
  and validated there. The cache is in memory and is refreshed from the closed
  M15 bars of each L1 snapshot. No REST call happens per signal.
- Any gap, count mismatch or lag behind the latest closed bar means NOT READY.
  The caller then emits no ETH C1 for that pass. Other combos keep running.
- REST warm-up retries are rate limited (`retry_sec`).
"""
from __future__ import annotations

import logging
import threading
import time

import pandas as pd

log = logging.getLogger("autotrade_v3.l2.eth_c1_history")

STEP_15M_MS = 15 * 60_000
ETH = "ETH-USDT"
COLS = ["open_time", "open", "high", "low", "close", "volume"]


class HistoryNotReady(RuntimeError):
    pass


def _contiguous(frame: pd.DataFrame, bars: int) -> bool:
    if len(frame) != bars:
        return False
    times = frame["open_time"].to_numpy(dtype="int64")
    return bool((times[1:] - times[:-1] == STEP_15M_MS).all())


class EthC1History:
    def __init__(self, client, *, bars: int = 12_000, retry_sec: float = 60.0, now_ms=None):
        self.client = client
        self.bars = int(bars)
        self.retry_ms = int(retry_sec * 1000)
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._frame: pd.DataFrame | None = None
        self._last_attempt_ms: int | None = None
        self._lock = threading.Lock()

    def _merge(self, base: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame | None:
        parts = [f[COLS + (["close_time"] if "close_time" in f.columns else [])] for f in (base, new)
                 if f is not None and len(f)]
        if not parts:
            return None
        out = (pd.concat(parts, ignore_index=True)
               .drop_duplicates("open_time", keep="last")
               .sort_values("open_time")
               .tail(self.bars)
               .reset_index(drop=True))
        return out

    def frame_for(self, latest: pd.DataFrame) -> pd.DataFrame:
        """Return `bars` contiguous closed M15 bars ending at the latest snapshot bar, or raise HistoryNotReady."""
        latest_open = int(latest["open_time"].iloc[-1])
        with self._lock:
            if self._frame is not None:
                merged = self._merge(self._frame, latest)
                if merged is not None and _contiguous(merged, self.bars) and int(merged["open_time"].iloc[-1]) == latest_open:
                    self._frame = merged
                    return merged
                log.warning("V3_L2_ETH_HISTORY_RESET reason=gap_or_lag latest_open=%s", latest_open)
                self._frame = None

            now = int(self.now_ms())
            if self._last_attempt_ms is not None and now - self._last_attempt_ms < self.retry_ms:
                raise HistoryNotReady("đang chờ lần warm-up lịch sử ETH kế tiếp")
            self._last_attempt_ms = now
            try:
                hist = self.client.fetch_history(ETH, self.bars, now_ms=now)
            except Exception as exc:
                raise HistoryNotReady(f"không tải được lịch sử ETH M15 ({type(exc).__name__})") from exc
            merged = self._merge(hist, latest)
            if merged is None or not _contiguous(merged, self.bars) or int(merged["open_time"].iloc[-1]) != latest_open:
                raise HistoryNotReady("lịch sử ETH M15 chưa liền mạch hoặc chưa khớp nến mới nhất")
            self._frame = merged
            log.info("V3_L2_ETH_HISTORY_READY bars=%s latest_open=%s", len(merged), latest_open)
            return merged
