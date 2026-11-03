"""Compatibility import; implementation and lifecycle belong to AutoTrade V2."""
import sys
from autotrade_v2.analytics import scheduler as _implementation
sys.modules[__name__] = _implementation
