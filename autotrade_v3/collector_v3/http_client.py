"""HTTP client chỉ-đọc cho API công khai BingX (không dùng khóa giao dịch)."""
from __future__ import annotations

from decimal import Decimal
import json
import time
from typing import Any

import requests  # type: ignore[import-untyped]

BINGX_PUBLIC_BASE = "https://open-api.bingx.com"

# Chỉ cho phép các endpoint công khai, chỉ-đọc
ALLOWED_PUBLIC_PATHS = frozenset({
    "/openApi/swap/v3/quote/klines",
    "/openApi/swap/v2/quote/premiumIndex",
    "/openApi/swap/v2/quote/fundingRate",
    "/openApi/swap/v2/quote/contracts",
})


class RateLimitError(RuntimeError):
    """Lỗi khi BingX trả về 429 hoặc giới hạn tần suất."""


class NetworkError(RuntimeError):
    """Lỗi mạng hoặc máy chủ tạm thời sau khi đã thử lại có lùi dần."""


class BingXPublicClient:
    """Client GET công khai tới BingX, tôn trọng giới hạn tần suất và không dùng khóa."""

    def __init__(
        self,
        *,
        base_url: str = BINGX_PUBLIC_BASE,
        min_interval_sec: float = 0.5,
        timeout_sec: float = 20.0,
        max_retries: int = 4,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.min_interval_sec = max(0.0, float(min_interval_sec))
        self.timeout_sec = float(timeout_sec)
        self.max_retries = max(1, int(max_retries))
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "autotrade-v3-collector/1.0"})
        self._last_request_ts = 0.0

    def _pace(self) -> None:
        if self.min_interval_sec <= 0:
            return
        elapsed = time.monotonic() - self._last_request_ts
        wait = self.min_interval_sec - elapsed
        if wait > 0:
            time.sleep(wait)

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Gửi GET công khai và parse JSON với parse_float=Decimal."""
        if path not in ALLOWED_PUBLIC_PATHS:
            raise ValueError(f"Endpoint không nằm trong danh sách công khai được phép: {path}")

        url = f"{self.base_url}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self._pace()
            self._last_request_ts = time.monotonic()
            try:
                resp = self.session.get(url, params=clean_params, timeout=self.timeout_sec)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(min(8.0, 1.0 * (2**attempt)))
                    continue
                raise NetworkError(f"Lỗi kết nối mạng tới BingX ({path}): {exc}") from exc

            if resp.status_code == 429:
                raise RateLimitError(f"BingX giới hạn tần suất HTTP 429 tại {path}; dừng đợt thu để lần sau tiếp tục.")

            if resp.status_code in (500, 502, 503, 504):
                last_exc = NetworkError(f"BingX HTTP {resp.status_code} tại {path}")
                if attempt < self.max_retries - 1:
                    time.sleep(min(8.0, 1.0 * (2**attempt)))
                    continue
                raise last_exc

            if resp.status_code != 200:
                raise NetworkError(f"BingX HTTP {resp.status_code} tại {path}")

            try:
                payload = json.loads(resp.text, parse_float=Decimal)
            except (ValueError, TypeError) as exc:
                raise NetworkError(f"Phản hồi JSON không hợp lệ từ BingX ({path}): {exc}") from exc

            if not isinstance(payload, dict):
                raise NetworkError(f"Định dạng phản hồi BingX không phải dict tại {path}")

            code = str(payload.get("code", ""))
            if code in ("429", "100429", "1015"):
                raise RateLimitError(f"BingX trả về mã giới hạn tần suất code={code} tại {path}")
            if code == "100410":
                msg = str(payload.get("msg", ""))
                last_exc = NetworkError(f"BingX trả lỗi tạm thời code=100410 tại {path}: {msg}")
                if attempt < self.max_retries - 1:
                    time.sleep(min(10.0, 1.5 * (2**attempt)))
                    continue
                raise last_exc
            if code != "0":
                msg = str(payload.get("msg", ""))
                raise NetworkError(f"BingX trả lỗi code={code} tại {path}: {msg}")

            return payload

        raise NetworkError(f"Thất bại sau {self.max_retries} lần thử tại {path}: {last_exc}")
