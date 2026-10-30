"""GET-only BingX USDT perpetual private REST client."""
from __future__ import annotations
import hashlib
import hmac
import os
import time
from urllib.parse import urlencode
import requests

BASE = 'https://open-api.bingx.com'

# Strictly scoped historical list endpoints. Log successful data:null for audit.
EMPTY_LIST_ENDPOINTS = frozenset({
    '/openApi/swap/v2/trade/allOrders',
    '/openApi/swap/v2/trade/fillHistory',
    '/openApi/swap/v2/user/income',
    '/openApi/swap/v2/trade/forceOrders',
    '/openApi/swap/v1/trade/positionHistory',
})


class BingXReadClient:
    def __init__(self, key=None, secret=None):
        self.key = key or os.getenv('BINGX_READ_API_KEY')
        self.secret = secret or os.getenv('BINGX_READ_API_SECRET')
        if not self.key or not self.secret:
            raise ValueError('Set BINGX_READ_API_KEY and BINGX_READ_API_SECRET (read-only).')
        self.http = requests.Session()
        self.http.headers.update({'X-BX-APIKEY': self.key})
        self.min_delay = float(os.getenv('BINGX_REQUEST_DELAY', '0.6'))
        self.last_request = 0.0
        self.null_data_counts = {}

    def get(self, path: str, params: dict):
        if not path.startswith('/openApi/swap/'):
            raise ValueError('Only BingX swap GET endpoints allowed')
        params = {k: v for k, v in params.items() if v is not None}
        for attempt in range(5):
            params['timestamp'] = int(time.time() * 1000)
            params['recvWindow'] = 5000
            encoded = urlencode(sorted(params.items()))
            sig = hmac.new(self.secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
            delay = self.min_delay - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
            self.last_request = time.monotonic()
            try:
                r = self.http.get(BASE + path, params=encoded + '&signature=' + sig, timeout=20)
                if r.status_code in (429, 500, 502, 503, 504):
                    if attempt < 4:
                        time.sleep(2 ** attempt)
                        continue
                r.raise_for_status()
                payload = r.json()
            except (requests.RequestException, ValueError):
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f'BingX request failed: {path}') from None
            if not isinstance(payload, dict):
                raise RuntimeError(f'BingX invalid response envelope: {path}')
            if str(payload.get('code', 'missing')) != '0':
                # Avoid logging account-related messages on public Actions runs.
                raise RuntimeError(f'BingX API error: {path} code={payload.get("code")}')
            if 'data' not in payload:
                raise RuntimeError(f'BingX success response missing data field: {path}')
            data = payload['data']
            if data is None:
                if path not in EMPTY_LIST_ENDPOINTS:
                    raise RuntimeError(f'Unexpected BingX null data at: {path}')
                self.null_data_counts[path] = self.null_data_counts.get(path, 0) + 1
                return []
            return data
        raise RuntimeError(f'BingX request failed: {path}')


def extract_list(data, candidates):
    """Accept documented BingX response aliases; never log private fill contents.

    SDK versions have used 'fill_history_orders', while current API docs
    describe 'fill_orders'. Missing arrays must not silently become zero
    trades unless the API explicitly confirms total=0.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in candidates:
            value = data.get(key)
            if isinstance(value, list):
                return value
            if key in data and value is None and data.get('total') in (0, '0'):
                return []
        # Printing the field names is enough to diagnose a future API change
        # without sending raw account records to public GitHub Actions logs.
        keys = sorted(str(k)[:60] for k in data.keys())[:25]
        raise ValueError(
            f'Unexpected BingX response structure: dict keys={keys}; expected={candidates}'
        )
    raise ValueError(
        f'Unexpected BingX response structure: type={type(data).__name__}; expected={candidates}'
    )


def fetch_window_split(client, path, params, keys, max_items, start_key, end_key, *, minimum_ms=1000):
    """Split saturated unpaginated responses so data cannot silently truncate."""
    data = extract_list(client.get(path, params), keys)
    if len(data) < max_items:
        return data
    lo, hi = int(params[start_key]), int(params[end_key])
    if hi - lo <= minimum_ms:
        raise RuntimeError(f'Possible pagination truncation: {path}, {lo}..{hi}')
    mid = (lo + hi) // 2
    left = fetch_window_split(client, path, {**params, end_key: mid}, keys, max_items, start_key, end_key, minimum_ms=minimum_ms)
    right = fetch_window_split(client, path, {**params, start_key: mid + 1}, keys, max_items, start_key, end_key, minimum_ms=minimum_ms)
    return left + right


def paged(client, path, params, keys, size):
    result = []
    for page in range(1, 501):
        items = extract_list(client.get(path, {**params, 'pageIndex': page, 'pageSize': size}), keys)
        result.extend(items)
        if len(items) < size:
            return result
    raise RuntimeError('Too many pagination pages; refusing incomplete sync')
