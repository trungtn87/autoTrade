"""Remove secrets from anything stored in control_logs / execution JSON."""
from __future__ import annotations

import json
import math
import re
from typing import Any

_SECRET_KEYS = re.compile(r"(signature|secret|api[-_]?key|apikey|x-bx-apikey|password|token|webhook|authorization)", re.I)
_URL = re.compile(r"https?://\S+")
_SIG = re.compile(r"(signature=)[0-9a-fA-F]+")


def clean_text(value: Any, limit: int = 4000) -> str:
    text = str(value)
    text = _SIG.sub(r"\1[REDACTED]", text)
    text = _URL.sub(lambda m: _redact_url(m.group(0)), text)
    return text[:limit]


def _redact_url(url: str) -> str:
    if "discord" in url.lower() or "webhook" in url.lower():
        return "[WEBHOOK_URL]"
    return url.split("?", 1)[0] + ("?[QUERY]" if "?" in url else "")


def clean(value: Any, depth: int = 0) -> Any:
    """JSON-safe deep copy with secret keys and webhook/signed URLs redacted."""
    if depth > 12:
        return "[DEPTH]"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k)
            out[key] = "[REDACTED]" if _SECRET_KEYS.search(key) else clean(v, depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [clean(v, depth + 1) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, str):
        return clean_text(value)
    return clean_text(repr(value))


def dumps(value: Any) -> str:
    return json.dumps(clean(value), ensure_ascii=False, default=str)
