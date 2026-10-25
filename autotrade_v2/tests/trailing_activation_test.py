"""Release activation defaults and reversible Render environment overrides."""
from dataclasses import replace
from unittest.mock import patch

from app.config import Settings, enable_runtime_trailing, validate_settings


def main():
    # A deployed V2 without explicit Render overrides now enables both legs.
    with patch.dict("os.environ",{},clear=True):
        live=enable_runtime_trailing(Settings())
        assert live.tp_trailing_split_enabled is True
        assert live.position_lifecycle_enabled is True
        assert live.tp_trailing_callback_rate==0.005
        validate_settings(live)

    # The Render kill switch always wins; lifecycle may remain on.
    with patch.dict("os.environ",{"TP_TRAILING_SPLIT_ENABLED":"false"},clear=True):
        settings=enable_runtime_trailing(Settings())
        assert settings.tp_trailing_split_enabled is False
        assert settings.position_lifecycle_enabled is True
        validate_settings(settings)

    # A disabled lifecycle also disables 50/50 so no unprotected split runs.
    with patch.dict("os.environ",{
        "TP_TRAILING_SPLIT_ENABLED":"true",
        "POSITION_LIFECYCLE_ENABLED":"false",
    },clear=True):
        settings=enable_runtime_trailing(Settings())
        assert settings.tp_trailing_split_enabled is False
        assert settings.position_lifecycle_enabled is False
        validate_settings(settings)

    # Explicitly enabling both remains supported, independent of defaults.
    with patch.dict("os.environ",{
        "TP_TRAILING_SPLIT_ENABLED":"true",
        "POSITION_LIFECYCLE_ENABLED":"true",
        "TP_TRAILING_CALLBACK_RATE":"0.008",
    },clear=True):
        settings=enable_runtime_trailing(Settings())
        assert settings.tp_trailing_split_enabled is True
        assert settings.position_lifecycle_enabled is True
        assert settings.tp_trailing_callback_rate==0.008
        validate_settings(settings)

    # Unit test Settings() keeps its staged defaults; main.py opt-in only.
    with patch.dict("os.environ",{},clear=True):
        base=Settings()
        assert base.tp_trailing_split_enabled is False
        assert base.position_lifecycle_enabled is False

    print({"ok":True,"runtime_enabled":True,"env_overrides":True,"fail_closed_dependency":True})


if __name__=="__main__":
    main()
