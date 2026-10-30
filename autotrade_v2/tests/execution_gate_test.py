from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from app.config import Settings
from app.contracts import TradeIntent
from app.execution.service import ExecutionService
from app.execution.store import ExecutionStore


def main() -> None:
    # Old hosting switches cannot enable trading without real secrets, or
    # disable it once an explicitly provisioned engine has complete credentials.
    env = dict(os.environ)
    for name in ("DATABASE_URL", "BINGX_API_KEY", "BINGX_API_SECRET"):
        env.pop(name, None)
    env.update(EXECUTION_ENABLED="true", DRY_RUN="false", WEBSOCKET_ENABLED="true")
    probe = (
        "from app.config import Settings; "
        "s=Settings(); "
        "print(int(s.execution_enabled), int(s.dry_run), int(s.websocket_enabled))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], env=env,
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "0 0 0", result.stdout

    env.update(
        DATABASE_URL="postgresql://user:password@db.supabase.co:5432/postgres",
        BINGX_API_KEY="FAKE_KEY",
        BINGX_API_SECRET="FAKE_SECRET",
        EXECUTION_ENABLED="false",
        DRY_RUN="true",
        WEBSOCKET_ENABLED="false",
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], env=env,
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "1 0 1", result.stdout

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        settings = Settings()
        assert settings.execution_enabled is False
        store = ExecutionStore(path)
        service = ExecutionService(settings, store)
        intent = TradeIntent(
            event_id="BTC-USDT|15m|C1|BUY|1",
            symbol="BTC-USDT",
            combo=1,
            side="BUY",
            timeframe="15m",
            close_time=1,
            entry=100.0,
            tp=102.0,
            sl=99.2,
            smc_dir=1,
        )
        result = service.execute(intent)
        assert result.status == "execution_disabled"
        assert result.accepted is False
        assert result.details["network_called"] is False
        assert store.seen(intent.event_id) is False
        print({"ok": True, "execution_gate": "closed", "network_called": False})
    finally:
        os.unlink(path)


if __name__ == "__main__":
    main()
