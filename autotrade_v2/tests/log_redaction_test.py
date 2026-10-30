"""No-network test: all formatted BingX URLs hide signed request queries."""
from __future__ import annotations

import io
import logging

from app.control.logging import SafeRequestFormatter,configure_logging,redact_bingx_url


def main():
    signed=(
        "https://open-api.bingx.com/openApi/swap/v3/user/balance?"
        "timestamp=1792938560091&signature=supersecret-signature&"
        "clientOrderId=atv2e-sensitive"
    )
    assert redact_bingx_url(signed).endswith("?[query-redacted]")
    assert "supersecret" not in redact_bingx_url(signed)
    assert "atv2e-sensitive" not in redact_bingx_url(signed)
    assert redact_bingx_url("https://example.org/path?normal=value")==(
        "https://example.org/path?normal=value"
    )

    configure_logging("INFO")
    assert logging.getLogger("httpx").getEffectiveLevel()>=logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel()>=logging.WARNING
    assert all(isinstance(h.formatter,SafeRequestFormatter)
               for h in logging.getLogger().handlers)

    output=io.StringIO()
    handler=logging.StreamHandler(output)
    handler.setFormatter(SafeRequestFormatter(
        "%(levelname)s %(name)s | %(message)s"
    ))
    root=logging.getLogger()
    root.addHandler(handler)
    try:
        logging.getLogger("httpx").info("HTTP Request: GET %s 200",signed)
        assert output.getvalue()=="","INFO HTTP client logging must be suppressed"
        logging.getLogger("httpx").warning("GET %s status=429",signed)
        logging.getLogger("autotrade_v2").info("signed URL in event: %s",signed)
        try:
            raise RuntimeError(f"failed to connect to {signed}")
        except RuntimeError:
            logging.getLogger("autotrade_v2").exception("BingX transport failed")
        logs=output.getvalue()
        assert logs.count("?[query-redacted]")==3,logs
        assert "supersecret" not in logs,logs
        assert "1792938560091" not in logs,logs
        assert "atv2e-sensitive" not in logs,logs
        assert "status=429" in logs and "transport failed" in logs,logs
    finally:
        root.removeHandler(handler)
        handler.close()
    print({"ok":True,"httpx_info_suppressed":True,
           "signed_url_redacted":True,"traceback_redacted":True})


if __name__=="__main__":
    main()
