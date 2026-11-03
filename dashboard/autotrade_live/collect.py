"""Compatibility import; implementation and lifecycle belong to AutoTrade V2."""
import sys
if __name__ == '__main__':
    raise SystemExit('Moved: run python -m autotrade_v2.analytics.collect from the repository root')
from autotrade_v2.analytics import collect as _implementation
sys.modules[__name__] = _implementation
