from __future__ import annotations

# Final production selection from long-history comparison:
# BASE -> Layer1 -> Layer2 -> Layer3.
# Keep only cases with non-negative PnL on 6M, 1Y, 3Y and FULL;
# choose the highest 3Y PnL within those valid stages.
#
# Exit architecture for every surviving winner is Layer1 two-tier trailing.
# Percent values below are decimals (0.009 == 0.9%).

FINAL_CASES: dict[str, dict[int, dict]] = {
    "BTC-USDT": {
        1: {
            "stage": "L3",
            "entry": {"mfi_long": 50.0, "mfi_short": 50.0},
            "layer2": "STRICT",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.003,
            "t2_activation_pct": 0.012,
            "t2_callback_pct": 0.005,
            "protect_pct": 0.0,
        },
        2: {
            "stage": "L2",
            "entry": {"adx": 20.0},
            "layer2": "VETO_OB",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.005,
            "t2_activation_pct": 0.012,
            "t2_callback_pct": 0.008,
            "protect_pct": 0.0,
        },
        3: {
            "stage": "L3",
            "entry": {"adx": 26.0},
            "layer2": "VETO",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.005,
            "t2_activation_pct": 0.012,
            "t2_callback_pct": 0.008,
            "protect_pct": 0.0,
        },
        4: {
            "stage": "L2",
            "entry": {"adx": 23.0},
            "layer2": "VETO",
            "sl_pct": 0.005,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.005,
            "t2_activation_pct": 0.012,
            "t2_callback_pct": 0.005,
            "protect_pct": 0.0,
        },
        6: {
            "stage": "L3",
            "entry": {"body_atr": 1.4},
            "layer2": "VETO_OB",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.005,
            "t2_activation_pct": 0.012,
            "t2_callback_pct": 0.005,
            "protect_pct": 0.0,
        },
        7: {
            "stage": "L3",
            "entry": {"body_atr": 1.6, "volume_mult": 2.0},
            "layer2": "OFF",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.005,
            "t2_activation_pct": 0.012,
            "t2_callback_pct": 0.005,
            "protect_pct": 0.002,
        },
        9: {
            "stage": "L3",
            "entry": {"rsi_long_max": 45.0, "rsi_short_min": 55.0, "fix_short_supertrend": True},
            "layer2": "VETO",
            "sl_pct": 0.005,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.003,
            "t2_activation_pct": 0.010,
            "t2_callback_pct": 0.008,
            "protect_pct": 0.0,
        },
        10: {
            "stage": "L3",
            "entry": {"adx": 22.0},
            "layer2": "VETO",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.005,
            "t2_activation_pct": 0.010,
            "t2_callback_pct": 0.005,
            "protect_pct": 0.002,
        },
        11: {
            "name": "TIER",
            "stage": "L3",
            "entry": {"adx": 30.0, "tier2_score": 7.0, "tier3_score": 5.0, "volume_mult": 1.3},
            "layer2": "OFF",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.003,
            "t2_activation_pct": 0.012,
            "t2_callback_pct": 0.008,
            "protect_pct": 0.0,
        },
    },
    "ETH-USDT": {
        1: {
            "stage": "L3",
            "entry": {"mfi_long": 50.0, "mfi_short": 50.0},
            "layer2": "VETO",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.003,
            "t2_activation_pct": 0.010,
            "t2_callback_pct": 0.005,
            "protect_pct": 0.0,
        },
        2: {
            "stage": "L2",
            "entry": {"adx": 20.0},
            "layer2": "VETO_OB",
            "sl_pct": 0.007,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.003,
            "t2_activation_pct": 0.010,
            "t2_callback_pct": 0.005,
            "protect_pct": 0.002,
        },
        4: {
            "stage": "L2",
            "entry": {"adx": 23.0},
            "layer2": "VETO",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.003,
            "t2_activation_pct": 0.012,
            "t2_callback_pct": 0.005,
            "protect_pct": 0.0,
        },
        5: {
            "stage": "L3",
            "entry": {"volume_mult": 2.2},
            "layer2": "STRICT",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.003,
            "t2_activation_pct": 0.010,
            "t2_callback_pct": 0.005,
            "protect_pct": 0.0,
        },
        6: {
            "stage": "L3",
            "entry": {"body_atr": 1.8},
            "layer2": "VETO",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.003,
            "t2_activation_pct": 0.012,
            "t2_callback_pct": 0.008,
            "protect_pct": 0.0,
        },
        7: {
            "stage": "L3",
            "entry": {"body_atr": 1.3, "volume_mult": 1.5},
            "layer2": "OB",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.003,
            "t2_activation_pct": 0.010,
            "t2_callback_pct": 0.008,
            "protect_pct": 0.0,
        },
        8: {
            "stage": "L2",
            "entry": {"adx": 25.0},
            "layer2": "STRICT",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.003,
            "t2_activation_pct": 0.008,
            "t2_callback_pct": 0.005,
            "protect_pct": 0.002,
        },
        10: {
            "stage": "L3",
            "entry": {"adx": 28.0},
            "layer2": "STRICT",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.003,
            "t2_activation_pct": 0.012,
            "t2_callback_pct": 0.008,
            "protect_pct": 0.0,
        },
        11: {
            "name": "TIER",
            "stage": "L3",
            "entry": {"adx": 25.0, "tier2_score": 7.0, "tier3_score": 7.0, "volume_mult": 1.8},
            "layer2": "STRICT_OB",
            "sl_pct": 0.009,
            "t1_activation_pct": 0.005,
            "t1_callback_pct": 0.003,
            "t2_activation_pct": 0.008,
            "t2_callback_pct": 0.008,
            "protect_pct": 0.0,
        },
    },
}

DISABLED_CASES = {
    "BTC-USDT": {5: "negative_window", 8: "negative_window"},
    "ETH-USDT": {3: "negative_window", 9: "negative_window"},
}


def case_name(combo: int) -> str:
    return "TIER" if int(combo) == 11 else f"C{int(combo)}"


def get_case(symbol: str, combo: int) -> dict | None:
    return FINAL_CASES.get(symbol.upper(), {}).get(int(combo))


def enabled_combos(symbol: str) -> tuple[int, ...]:
    return tuple(sorted(FINAL_CASES.get(symbol.upper(), {})))


def snapshot() -> dict:
    return {
        "version": "FINAL18_2026-09-21",
        "selection_rule": "best valid BASE/L1/L2/L3 stage by 3Y PnL; all 6M/1Y/3Y/FULL >= 0",
        "enabled": {
            symbol: [case_name(c) for c in enabled_combos(symbol)]
            for symbol in sorted(FINAL_CASES)
        },
        "disabled": {
            symbol: {case_name(c): reason for c, reason in sorted(items.items())}
            for symbol, items in sorted(DISABLED_CASES.items())
        },
        "exit_architecture": "two_tier_trailing_50_50_no_fixed_tp",
    }
