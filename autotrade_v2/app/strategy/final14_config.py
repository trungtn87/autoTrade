from __future__ import annotations

# FINAL14 is locked to RR TP<=2% backtest run 35623101331.
# Values are percentages expressed as decimals (0.02 == 2.0%).
# Selection rule: PnL >= 0 on 6M, 1Y, 3Y and FULL; maximize 3Y PnL.

FINAL14_CASES: dict[str, dict[int, dict]] = {
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

# NEW6 research additions. IDs 101..106 are deliberately outside the FINAL14
# combo namespace so FINAL14 stays locked and independently auditable.
# All NEW6 signals are native 1H and execute at the confirmed 1H close.
NEW6_CASES: dict[str, dict[int, dict]] = {
    "BTC-USDT": {
        101:{"name":"N1","indicator":"DeMarker","entry_variant":"trend_n60_0.25_0.75","layer2":"SMC_STRICT_OB","tp_pct":0.018,"sl_pct":0.009,"rr":2.0},
        104:{"name":"N4","indicator":"Ichimoku","entry_variant":"tkcross_12_30_60","layer2":"OB","tp_pct":0.020,"sl_pct":0.006666666666666667,"rr":3.0},
        106:{"name":"N6","indicator":"Vortex","entry_variant":"cross_n14_r1.2","layer2":"OB","tp_pct":0.011,"sl_pct":0.009,"rr":0.011/0.009},
    },
    "ETH-USDT": {
        102:{"name":"N2","indicator":"Aroon","entry_variant":"cross_n40_t70","layer2":"SMC_VETO_OB","tp_pct":0.018,"sl_pct":0.009,"rr":2.0},
        103:{"name":"N3","indicator":"CMO","entry_variant":"momcross_n40_t40","layer2":"OFF","tp_pct":0.020,"sl_pct":0.008,"rr":2.5},
        105:{"name":"N5","indicator":"KAMA_ER","entry_variant":"state_e40_s40_t0.4","layer2":"OB","tp_pct":0.020,"sl_pct":0.008,"rr":2.5},
    },
}

DISABLED_CASES = {
    "BTC-USDT": {3,5,8},
    "ETH-USDT": {2,5,8,9,11},
}

NATIVE_TIMEFRAME = {
    1:"1h",2:"1h",3:"1h",4:"1h",5:"15m",6:"1h",
    7:"15m",8:"15m",9:"15m",10:"15m",11:"1h",
}


def case_name(combo:int)->str:
    combo=int(combo)
    if 101 <= combo <= 106:
        return f"N{combo-100}"
    return "TIER" if combo==11 else f"C{combo}"


def get_case(symbol:str, combo:int)->dict|None:
    symbol=symbol.upper()
    combo=int(combo)
    return (
        FINAL14_CASES.get(symbol,{}).get(combo)
        or NEW6_CASES.get(symbol,{}).get(combo)
    )


def enabled_combos(symbol:str)->tuple[int,...]:
    """Locked FINAL14 combo IDs only."""
    return tuple(sorted(FINAL14_CASES.get(symbol.upper(),{})))


def enabled_new6_combos(symbol:str)->tuple[int,...]:
    return tuple(sorted(NEW6_CASES.get(symbol.upper(),{})))


def new6_snapshot()->dict:
    return {
        "version":"NEW6_2026-09-25",
        "native_timeframe":"1h",
        "exit":"100% hard TP + 100% hard SL; no trailing; no partial exit",
        "enabled":{
            s:[case_name(c) for c in enabled_new6_combos(s)]
            for s in sorted(NEW6_CASES)
        },
        "cases":{
            s:{case_name(c):dict(cfg) for c,cfg in sorted(cases.items())}
            for s,cases in sorted(NEW6_CASES.items())
        },
    }


def snapshot()->dict:
    return {
        "version":"FINAL14_RR_TP2_2026-09-21",
        "source_run_id":35623101331,
        "source_artifact_id":10650213856,
        "exit":"100% hard TP + 100% hard SL; no trailing; no partial exit",
        "selection":"PnL >= 0 on 6M/1Y/3Y/FULL; maximize 3Y PnL",
        "enabled":{s:[case_name(c) for c in enabled_combos(s)] for s in sorted(FINAL14_CASES)},
        "disabled":{s:[case_name(c) for c in sorted(v)] for s,v in sorted(DISABLED_CASES.items())},
    }
