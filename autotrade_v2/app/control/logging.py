from __future__ import annotations

import logging
import re


# BingX signed request URLs can be logged by httpx, exceptions, or an
# application logger. Strip the ENTIRE query, not just the HMAC: parameters
# may also include clientOrderId, timestamps or other account metadata.
_BINGX_QUERY = re.compile(
    r"""(https?://(?:[a-z0-9-]+\.)*bingx\.com/[^\s?'"<>]+)\?[^\s'"<>]+""",
    re.IGNORECASE,
)


def redact_bingx_url(text: str) -> str:
    return _BINGX_QUERY.sub(r"\1?[query-redacted]", text)


class SafeRequestFormatter(logging.Formatter):
    """Redact even exception tracebacks after standard logging formatting."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_bingx_url(super().format(record))


def configure_logging(level: str = "INFO") -> None:
    template = "%(asctime)s %(levelname)s %(name)s | %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=template,
        force=True,
    )
    # No HTTP request URLs in routine logs (especially signed BingX URLs).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # Defense in depth: warning/error records from ANY logger, including
    # traceback text, still cannot emit query strings from BingX URLs.
    for handler in logging.getLogger().handlers:
        handler.setFormatter(SafeRequestFormatter(template))
