from __future__ import annotations

import os
import tempfile

from app.config import Settings
from app.contracts import TradeIntent
from app.execution.service import ExecutionService
from app.execution.store import ExecutionStore


def main() -> None:
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
