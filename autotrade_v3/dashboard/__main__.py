"""Một lệnh chạy dashboard V3-16 trên loopback."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["serve"]:
        print("Cách dùng: python -m autotrade_v3.dashboard serve", file=sys.stderr)
        return 2
    from streamlit.web import cli as stcli

    app_path = Path(__file__).with_name("app.py")
    port = int(os.environ.get("AUTOTRADE_DASHBOARD_PORT", "8501"))
    if not 1 <= port <= 65535:
        raise ValueError("AUTOTRADE_DASHBOARD_PORT must be between 1 and 65535")
    sys.argv = [
        "streamlit",
        "run",
        str(app_path),
        "--server.address=127.0.0.1",
        f"--server.port={port}",
        "--server.headless=true",
        "--server.fileWatcherType=none",
        "--browser.gatherUsageStats=false",
        "--theme.base=dark",
        "--theme.backgroundColor=#080d16",
        "--theme.secondaryBackgroundColor=#172235",
        "--theme.textColor=#f8fafc",
    ]
    return int(stcli.main())


if __name__ == "__main__":
    raise SystemExit(main())
