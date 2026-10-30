"""Retired SL replacement must stay disabled; minimal orphan cleanup is separate."""
from pathlib import Path
from unittest.mock import patch

from app.config import Settings,enable_runtime_trailing,validate_settings
from app.execution.service import ExecutionService


def main():
    with patch.dict("os.environ",{
        "POSITION_LIFECYCLE_ENABLED":"true",
        "TP_TRAILING_SPLIT_ENABLED":"true",
    },clear=True):
        settings=enable_runtime_trailing(Settings())
        assert settings.position_lifecycle_enabled is False
        assert settings.exit_cleanup_enabled is True
        assert settings.tp_trailing_split_enabled is True
        validate_settings(settings)

    root=Path(__file__).resolve().parents[1]
    main_py=(root/"app"/"main.py").read_text("utf-8")
    service_py=(root/"app"/"execution"/"service.py").read_text("utf-8")
    assert "execution_service.reconcile_open_positions(" not in main_py
    assert "execution_service.recover_pending()" in main_py
    assert "execution_service.cleanup_closed_exits()" in main_py
    assert ".cancel_replace_stop_loss(" not in service_py
    assert "self._lifecycle" not in service_py
    assert "EXIT_CLEANUP_POLL_SEC=60.0" in main_py
    print({"ok":True,"post_entry_sl_replacement_disabled":True,
           "orphan_order_cleanup_enabled":True})


if __name__=="__main__":
    main()
