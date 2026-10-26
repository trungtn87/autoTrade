"""Exchange-native trailing remains enabled while bot-owned lifecycle is retired."""
from dataclasses import replace
from unittest.mock import patch

from app.config import Settings, enable_runtime_trailing, validate_settings


def main():
    # In production the 50/50 exit split is independent of old lifecycle flags.
    for env in ({}, {"POSITION_LIFECYCLE_ENABLED":"true"},
                {"POSITION_LIFECYCLE_ENABLED":"false"}):
        with patch.dict("os.environ",env,clear=True):
            settings=enable_runtime_trailing(Settings())
            assert settings.tp_trailing_split_enabled is True
            assert settings.position_lifecycle_enabled is False
            assert settings.tp_trailing_activation_pct==0.005
            assert settings.tp_trailing_callback_rate==0.005
            validate_settings(settings)

    with patch.dict("os.environ",{
        "TP_TRAILING_SPLIT_ENABLED":"false",
        "POSITION_LIFECYCLE_ENABLED":"true",
    },clear=True):
        settings=enable_runtime_trailing(Settings())
        assert settings.tp_trailing_split_enabled is False
        assert settings.position_lifecycle_enabled is False
        validate_settings(settings)

    with patch.dict("os.environ",{
        "TP_TRAILING_SPLIT_ENABLED":"true",
        "TP_TRAILING_CALLBACK_RATE":"0.008",
        "TP_TRAILING_ACTIVATION_PCT":"0.007",
    },clear=True):
        settings=enable_runtime_trailing(Settings())
        assert settings.tp_trailing_split_enabled is True
        assert settings.position_lifecycle_enabled is False
        assert settings.tp_trailing_callback_rate==0.008
        assert settings.tp_trailing_activation_pct==0.007
        validate_settings(settings)

    try:
        validate_settings(replace(settings,tp_trailing_activation_pct=0))
        assert False,"zero activation must fail validation"
    except ValueError:
        pass

    with patch.dict("os.environ",{},clear=True):
        base=Settings()
        assert base.tp_trailing_split_enabled is False
        assert base.position_lifecycle_enabled is False

    print({"ok":True,"exchange_native_trailing":True,
           "post_entry_lifecycle_disabled":True})


if __name__=="__main__":
    main()
