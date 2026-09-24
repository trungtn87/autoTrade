from __future__ import annotations


class AutoTradeError(RuntimeError):
    category = "SYSTEM_ERROR"


class DataLayerError(AutoTradeError):
    category = "DATA_ERROR"


class StrategyLayerError(AutoTradeError):
    category = "STRATEGY_ERROR"


class ExecutionLayerError(AutoTradeError):
    category = "EXECUTION_ERROR"


class BingXApiError(ExecutionLayerError):
    category = "BINGX_API_ERROR"

    def __init__(self, code, message: str, endpoint: str, params: dict | None = None):
        safe = {
            k: v for k, v in (params or {}).items()
            if k not in {"signature", "timestamp", "recvWindow"}
        }
        super().__init__(f"BingX error {code}: {message} | endpoint={endpoint} | params={safe}")
        self.code = str(code)
        self.message = message
        self.endpoint = endpoint
        self.params = safe


class ConfigError(AutoTradeError):
    category = "CONFIG_ERROR"


def classify_error(exc: BaseException) -> str:
    return getattr(exc, "category", "SYSTEM_ERROR")
