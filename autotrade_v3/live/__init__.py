"""AutoTrade V3 live trading service (five layers).

L1 data -> L2 strategy (20FINAL, unchanged) -> L3 execution -> L4 control
(read-only BingX + control_logs) -> L5 alert (Discord BTC/ETH/ERROR).

This package is separate from the offline research lab in ``autotrade_v3``.
It reuses the existing V2 connection code (BingX transport, kline stream,
historical REST, 20FINAL strategy modules) without modifying it.
"""
