from __future__ import annotations

# Immutable FINAL14 decision manifest copied from the locked backtest selection
# run 35623101331. Production strategy/config changes must not modify this file
# as part of ordinary implementation work. If a future strategy version is
# intentionally changed, it must receive a new manifest/version instead.
LOCKED_STRATEGY_VERSION = "FINAL14_RR_TP2_2026-09-21"
LOCKED_SOURCE_RUN_ID = 35623101331
LOCKED_SOURCE_ARTIFACT_ID = 10650213856

LOCKED_FINAL14_CASES = {
    "BTC-USDT": {
        1: {"entry_variant":"MFI_L50_S50","entry":{"mfi_long":50.0,"mfi_short":50.0},"layer2":"SMC_STRICT","tp_pct":0.020,"sl_pct":0.008,"rr":2.5},
        2: {"entry_variant":"ADX_20","entry":{"adx":20.0},"layer2":"SMC_VETO_OB","tp_pct":0.012,"sl_pct":0.008,"rr":1.5},
        4: {"entry_variant":"ADX_23","entry":{"adx":23.0},"layer2":"SMC_VETO","tp_pct":0.018,"sl_pct":0.009,"rr":2.0},
        6: {"entry_variant":"BODY_1.4","entry":{"body_atr":1.4},"layer2":"SMC_VETO_OB","tp_pct":0.020,"sl_pct":0.00666667,"rr":3.0},
        7: {"entry_variant":"BODY1.6_VOL2.0","entry":{"body_atr":1.6,"volume_mult":2.0},"layer2":"OFF","tp_pct":0.020,"sl_pct":0.008,"rr":2.5},
        9: {"entry_variant":"RSI45_FIXST","entry":{"rsi_long_max":45.0,"rsi_short_min":55.0,"fix_short_supertrend":True},"layer2":"SMC_VETO","tp_pct":0.010,"sl_pct":0.004,"rr":2.5},
        10:{"entry_variant":"ADX_22","entry":{"adx":22.0},"layer2":"SMC_VETO","tp_pct":0.010,"sl_pct":0.008,"rr":1.25},
        11:{"name":"TIER","entry_variant":"ADX30_T27_T35_V1.3","entry":{"adx":30.0,"tier2_score":7.0,"tier3_score":5.0,"volume_mult":1.3},"layer2":"OFF","tp_pct":0.018,"sl_pct":0.009,"rr":2.0},
    },
    "ETH-USDT": {
        1: {"entry_variant":"MFI_L50_S50","entry":{"mfi_long":50.0,"mfi_short":50.0},"layer2":"SMC_VETO","tp_pct":0.020,"sl_pct":0.00666667,"rr":3.0},
        3: {"entry_variant":"ADX_34","entry":{"adx":34.0},"layer2":"SMC_STRICT","tp_pct":0.018,"sl_pct":0.006,"rr":3.0},
        4: {"entry_variant":"ADX_23","entry":{"adx":23.0},"layer2":"SMC_VETO","tp_pct":0.020,"sl_pct":0.008,"rr":2.5},
        6: {"entry_variant":"BODY_1.8","entry":{"body_atr":1.8},"layer2":"SMC_VETO","tp_pct":0.020,"sl_pct":0.00666667,"rr":3.0},
        7: {"entry_variant":"BODY1.3_VOL1.5","entry":{"body_atr":1.3,"volume_mult":1.5},"layer2":"OB","tp_pct":0.018,"sl_pct":0.009,"rr":2.0},
        10:{"entry_variant":"ADX_28","entry":{"adx":28.0},"layer2":"SMC_STRICT","tp_pct":0.018,"sl_pct":0.009,"rr":2.0},
    },
}

LOCKED_NATIVE = {
    1:"1h",2:"1h",3:"1h",4:"1h",5:"15m",6:"1h",
    7:"15m",8:"15m",9:"15m",10:"15m",11:"1h",
}
