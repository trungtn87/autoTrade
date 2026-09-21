from __future__ import annotations

# FINAL14 is locked to RR TP<=2% backtest run 35623101331.
# Values are percentages expressed as decimals (0.02 == 2.0%).
# Selection rule: PnL >= 0 on 6M, 1Y, 3Y and FULL; maximize 3Y PnL.

FINAL14_CASES: dict[str, dict[int, dict]] = {
    "BTC-USDT": {
        1: {"entry_variant":"MFI_L50_S50","layer2":"SMC_STRICT","tp_pct":0.020,"sl_pct":0.008,"rr":2.5},
        2: {"entry_variant":"ADX_20","layer2":"SMC_VETO_OB","tp_pct":0.012,"sl_pct":0.008,"rr":1.5},
        4: {"entry_variant":"ADX_23","layer2":"SMC_VETO","tp_pct":0.018,"sl_pct":0.009,"rr":2.0},
        6: {"entry_variant":"BODY_1.4","layer2":"SMC_VETO_OB","tp_pct":0.020,"sl_pct":0.00666667,"rr":3.0},
        7: {"entry_variant":"BODY1.6_VOL2.0","layer2":"OFF","tp_pct":0.020,"sl_pct":0.008,"rr":2.5},
        9: {"entry_variant":"RSI45_FIXST","layer2":"SMC_VETO","tp_pct":0.010,"sl_pct":0.004,"rr":2.5},
        10:{"entry_variant":"ADX_22","layer2":"SMC_VETO","tp_pct":0.010,"sl_pct":0.008,"rr":1.25},
        11:{"name":"TIER","entry_variant":"ADX30_T27_T35_V1.3","layer2":"OFF","tp_pct":0.018,"sl_pct":0.009,"rr":2.0},
    },
    "ETH-USDT": {
        1: {"entry_variant":"MFI_L50_S50","layer2":"SMC_VETO","tp_pct":0.020,"sl_pct":0.00666667,"rr":3.0},
        3: {"entry_variant":"ADX_34","layer2":"SMC_STRICT","tp_pct":0.018,"sl_pct":0.006,"rr":3.0},
        4: {"entry_variant":"ADX_23","layer2":"SMC_VETO","tp_pct":0.020,"sl_pct":0.008,"rr":2.5},
        6: {"entry_variant":"BODY_1.8","layer2":"SMC_VETO","tp_pct":0.020,"sl_pct":0.00666667,"rr":3.0},
        7: {"entry_variant":"BODY1.3_VOL1.5","layer2":"OB","tp_pct":0.018,"sl_pct":0.009,"rr":2.0},
        10:{"entry_variant":"ADX_28","layer2":"SMC_STRICT","tp_pct":0.018,"sl_pct":0.009,"rr":2.0},
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
    return "TIER" if int(combo)==11 else f"C{int(combo)}"

def get_case(symbol:str, combo:int)->dict|None:
    return FINAL14_CASES.get(symbol.upper(),{}).get(int(combo))

def enabled_combos(symbol:str)->tuple[int,...]:
    return tuple(sorted(FINAL14_CASES.get(symbol.upper(),{})))

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
