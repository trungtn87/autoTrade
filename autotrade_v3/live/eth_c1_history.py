"""ETH-USDT M15 long history for the ETH C1 H1_BOTH_1 generator only.

Why: the Lab EMA150 4H state converges only on long history. With the live
3400-bar snapshot, 4H EMA values drift enough to flip BUY/SELL decisions
(measured on ETH M15 RUN17: 19 BUY and 2 SELL flips in 119 sampled hours).
With 12000 bars the drift is below 0.002 points on the same samples.

Rules:
- Other combos and V2 keep the 3400-bar snapshot. This cache is ETH C1 only.
- Warm-up loads the history in pages of `page_bars` (one BingX public klines
  request each, paced by the client's own rate limit), at most `pages_per_pass`
  pages per L2 pass, so no single pass sends a burst of requests. While the
  warm-up is incomplete no ETH C1 is emitted; other combos keep running.
  The cache is in memory and is refreshed from the closed M15 bars of each L1
  snapshot. No REST call happens per signal.
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
    def __init__(self, client, *, bars: int = 12_000, page_bars: int = 1000, pages_per_pass: int = 4,
                 retry_sec: float = 60.0, now_ms=None):
        self.client = client
        self.bars = int(bars)
        self.page_bars = max(1, min(int(page_bars), self.bars))
        self.pages_per_pass = max(1, int(pages_per_pass))
        self.retry_ms = int(retry_sec * 1000)
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._frame: pd.DataFrame | None = None
        self._last_attempt_ms: int | None = None
        self._lock = threading.Lock()
        # paged warm-up state (reset on any gap or lag)
        self._parts: list[pd.DataFrame] = []
        self._next_open: int | None = None
        self._stop_open: int | None = None
        self._first_open: int | None = None

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

    def _reset(self) -> None:
        self._frame = None
        self._parts = []
        self._next_open = self._stop_open = self._first_open = None

    def _page(self, latest_open: int) -> None:
        """Fetch the next page(s) of the warm-up range; raise HistoryNotReady while incomplete."""
        if self._next_open is None:
            self._first_open = latest_open - (self.bars - 1) * STEP_15M_MS
            self._stop_open = latest_open
            self._next_open = self._first_open
        for _ in range(self.pages_per_pass):
            if self._next_open > self._stop_open:
                break
            start = self._next_open
            end = min(start + (self.page_bars - 1) * STEP_15M_MS, self._stop_open)
            count = int((end - start) // STEP_15M_MS) + 1
            try:
                part = self.client.fetch_window(ETH, start, end + STEP_15M_MS - 1, count)
            except Exception as exc:
                # Keep the pages already loaded: a failed page only delays the warm-up, it never restarts it.
                # The BingX code and message are kept (bounded) so the control log shows the real cause.
                detail = " ".join(str(exc).split())[:300]
                raise HistoryNotReady(
                    f"không tải được đợt lịch sử ETH M15 {len(self._parts) + 1}/"
                    f"{-(-self.bars // self.page_bars)} ({type(exc).__name__}: {detail})") from exc
            self._parts.append(part[COLS])
            self._next_open = end + STEP_15M_MS
        if self._next_open <= self._stop_open:
            done = len(self._parts)
            total = -(-self.bars // self.page_bars)
            raise HistoryNotReady(f"đang tải lịch sử ETH M15 theo đợt: {done}/{total}")

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
                self._reset()

            now = int(self.now_ms())
            if self._last_attempt_ms is not None and now - self._last_attempt_ms < self.retry_ms:
                raise HistoryNotReady("đang chờ lần warm-up lịch sử ETH kế tiếp")
            self._last_attempt_ms = now
            self._page(latest_open)
            hist = (pd.concat(self._parts, ignore_index=True)
                    .drop_duplicates("open_time", keep="last").sort_values("open_time").reset_index(drop=True))
            merged = self._merge(hist, latest)
            if merged is None or not _contiguous(merged, self.bars) or int(merged["open_time"].iloc[-1]) != latest_open:
                self._reset()
                raise HistoryNotReady("lịch sử ETH M15 chưa liền mạch hoặc chưa khớp nến mới nhất")
            self._reset()
            self._frame = merged
            log.info("V3_L2_ETH_HISTORY_READY bars=%s latest_open=%s", len(merged), latest_open)
            return merged
