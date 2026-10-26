"""No-network check: bot never monitors/cancels/replaces completed BingX exits."""
from pathlib import Path
from unittest.mock import patch

from app.config import Settings,enable_runtime_trailing,validate_settings
from app.execution.service import ExecutionService


class Tripwire:
    def __getattr__(self,name):
        raise AssertionError(f"Exchange or store accessed during retired lifecycle: {name}")


def main():
    # Compatibility method cannot touch network/store, even if called by an
    # old worker or with a stale POSITION_LIFECYCLE_ENABLED environment value.
    service=object.__new__(ExecutionService)
    service.executor=Tripwire()
    service.store=Tripwire()
    service._execution_lock=Tripwire()
    service.settings=Tripwire()
    assert service.reconcile_open_positions()==[]

    with patch.dict("os.environ",{
        "POSITION_LIFECYCLE_ENABLED":"true",
        "TP_TRAILING_SPLIT_ENABLED":"true",
    },clear=True):
        settings=enable_runtime_trailing(Settings())
        assert settings.position_lifecycle_enabled is False
        assert settings.tp_trailing_split_enabled is True
        validate_settings(settings)

    # Runtime still retries unfinished entry + protection setup after restart,
    # but does not poll terminal positions every 15s.
    root=Path(__file__).resolve().parents[1]
    main_py=(root/"app"/"main.py").read_text("utf-8")
    service_py=(root/"app"/"execution"/"service.py").read_text("utf-8")
    assert "execution_service.reconcile_open_positions(" not in main_py
    assert "execution_service.recover_pending()" in main_py
    assert "persisted[\"lifecycle\"]=" not in service_py
    assert "self._lifecycle" not in service_py
    print({"ok":True,"no_post_entry_exchange_mutation":True,
           "unfinished_setup_recovery_preserved":True})


if __name__=="__main__":
    main()
