"""Intrabar ambiguity policies for M1 bar simulation (V3-13)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AmbiguityPolicy = Literal["LEGACY", "CONSERVATIVE", "OPTIMISTIC", "OHLC_PATH"]
AmbiguityType = Literal[
    "NONE",
    "TP_AND_SL",
    "SL_AND_TRAIL_ACT",
    "TRAIL_ACT_AND_CB",
    "TRAIL_PEAK_AND_STOP",
]

VALID_AMBIGUITY_POLICIES: tuple[AmbiguityPolicy, ...] = (
    "LEGACY",
    "CONSERVATIVE",
    "OPTIMISTIC",
    "OHLC_PATH",
)


def normalize_ambiguity_policy(policy: str) -> AmbiguityPolicy:
    p = policy.upper().strip()
    if p not in VALID_AMBIGUITY_POLICIES:
        raise ValueError(
            f"Unknown ambiguity_policy: {policy!r}. Expected one of {VALID_AMBIGUITY_POLICIES}"
        )
    return p  # type: ignore[return-value]


@dataclass(frozen=True)
class AmbiguityResolution:
    """Resolved outcome for one of the four intrabar ambiguity situations."""

    should_exit: bool
    exit_price: float = 0.0
    exit_reason: str = ""
    new_peak_price: float = 0.0
    new_trail_stop: float = 0.0
    ambiguity_type: AmbiguityType = "NONE"
    intrabar_ambiguous: bool = False
    intrabar_trailing_ambiguous: bool = False


def _favorable_extreme_first(is_buy: bool, bar_open: float, bar_close: float) -> bool:
    """For OHLC_PATH: True if the favorable extreme (H for BUY, L for SELL) precedes the adverse extreme."""
    bullish_or_doji = bar_close >= bar_open  # path: O -> L -> H -> C
    return (not bullish_or_doji) if is_buy else bullish_or_doji


def resolve_tp_and_sl(
    *,
    policy: AmbiguityPolicy,
    is_buy: bool,
    tp_price: float,
    sl_price: float,
    bar_open: float,
    bar_close: float,
) -> AmbiguityResolution:
    """Situation (i): both TP and SL are touched inside the same M1 bar."""
    if policy in ("LEGACY", "CONSERVATIVE"):
        return AmbiguityResolution(
            should_exit=True,
            exit_price=sl_price,
            exit_reason="SL_INTRABAR_AMBIGUOUS",
            ambiguity_type="TP_AND_SL",
            intrabar_ambiguous=True,
        )
    if policy == "OPTIMISTIC":
        return AmbiguityResolution(
            should_exit=True,
            exit_price=tp_price,
            exit_reason="TP",
            ambiguity_type="TP_AND_SL",
            intrabar_ambiguous=True,
        )
    # OHLC_PATH
    if _favorable_extreme_first(is_buy, bar_open, bar_close):
        return AmbiguityResolution(
            should_exit=True,
            exit_price=tp_price,
            exit_reason="TP",
            ambiguity_type="TP_AND_SL",
            intrabar_ambiguous=True,
        )
    return AmbiguityResolution(
        should_exit=True,
        exit_price=sl_price,
        exit_reason="SL_INTRABAR_AMBIGUOUS",
        ambiguity_type="TP_AND_SL",
        intrabar_ambiguous=True,
    )


def resolve_sl_and_trail_act(
    *,
    policy: AmbiguityPolicy,
    is_buy: bool,
    sl_price: float,
    fav_extreme: float,
    cb_pct: float,
    bar_open: float,
    bar_close: float,
) -> AmbiguityResolution:
    """Situation (ii): both SL and trailing activation level are touched inside the same M1 bar."""
    if policy in ("LEGACY", "CONSERVATIVE"):
        return AmbiguityResolution(
            should_exit=True,
            exit_price=sl_price,
            exit_reason="SL_INTRABAR_AMBIGUOUS",
            ambiguity_type="SL_AND_TRAIL_ACT",
            intrabar_ambiguous=True,
        )
    new_stop = (
        max(sl_price, fav_extreme * (1.0 - cb_pct))
        if is_buy
        else min(sl_price, fav_extreme * (1.0 + cb_pct))
    )
    if policy == "OPTIMISTIC":
        return AmbiguityResolution(
            should_exit=False,
            new_peak_price=fav_extreme,
            new_trail_stop=new_stop,
            ambiguity_type="SL_AND_TRAIL_ACT",
            intrabar_ambiguous=True,
        )
    # OHLC_PATH
    if _favorable_extreme_first(is_buy, bar_open, bar_close):
        # Favorable extreme activates trailing first, then adverse extreme hits new_stop (since it hit sl_price)
        return AmbiguityResolution(
            should_exit=True,
            exit_price=new_stop,
            exit_reason="TRAIL",
            new_peak_price=fav_extreme,
            new_trail_stop=new_stop,
            ambiguity_type="SL_AND_TRAIL_ACT",
            intrabar_ambiguous=True,
        )
    return AmbiguityResolution(
        should_exit=True,
        exit_price=sl_price,
        exit_reason="SL_INTRABAR_AMBIGUOUS",
        ambiguity_type="SL_AND_TRAIL_ACT",
        intrabar_ambiguous=True,
    )


def resolve_trail_act_and_cb(
    *,
    policy: AmbiguityPolicy,
    is_buy: bool,
    fav_extreme: float,
    new_stop: float,
    bar_open: float,
    bar_close: float,
) -> AmbiguityResolution:
    """Situation (iii): trailing activates and pulls back to callback in the same M1 bar."""
    if policy in ("LEGACY", "CONSERVATIVE"):
        return AmbiguityResolution(
            should_exit=True,
            exit_price=new_stop,
            exit_reason="TRAIL",
            new_peak_price=fav_extreme,
            new_trail_stop=new_stop,
            ambiguity_type="TRAIL_ACT_AND_CB",
            intrabar_trailing_ambiguous=True,
        )
    if policy == "OPTIMISTIC":
        return AmbiguityResolution(
            should_exit=False,
            new_peak_price=fav_extreme,
            new_trail_stop=new_stop,
            ambiguity_type="TRAIL_ACT_AND_CB",
            intrabar_trailing_ambiguous=True,
        )
    # OHLC_PATH
    if _favorable_extreme_first(is_buy, bar_open, bar_close):
        return AmbiguityResolution(
            should_exit=True,
            exit_price=new_stop,
            exit_reason="TRAIL",
            new_peak_price=fav_extreme,
            new_trail_stop=new_stop,
            ambiguity_type="TRAIL_ACT_AND_CB",
            intrabar_trailing_ambiguous=True,
        )
    # Adverse extreme occurred before activation -> stays open with new peak/stop
    return AmbiguityResolution(
        should_exit=False,
        new_peak_price=fav_extreme,
        new_trail_stop=new_stop,
        ambiguity_type="TRAIL_ACT_AND_CB",
        intrabar_trailing_ambiguous=True,
    )


def resolve_trail_peak_and_stop(
    *,
    policy: AmbiguityPolicy,
    is_buy: bool,
    old_stop: float,
    new_stop: float,
    fav_extreme: float,
    hit_old_stop: bool,
    bar_open: float,
    bar_close: float,
) -> AmbiguityResolution:
    """Situation (iv): while trailing, bar both sets a new peak and touches stop."""
    if policy in ("LEGACY", "CONSERVATIVE"):
        if hit_old_stop:
            return AmbiguityResolution(
                should_exit=True,
                exit_price=old_stop,
                exit_reason="TRAIL",
                new_peak_price=fav_extreme,
                new_trail_stop=old_stop,
                ambiguity_type="TRAIL_PEAK_AND_STOP",
                # LEGACY golden v3-12 had intrabar_trailing_ambiguous=False when hit_old_stop
                intrabar_trailing_ambiguous=(policy != "LEGACY"),
            )
        return AmbiguityResolution(
            should_exit=True,
            exit_price=new_stop,
            exit_reason="TRAIL",
            new_peak_price=fav_extreme,
            new_trail_stop=new_stop,
            ambiguity_type="TRAIL_PEAK_AND_STOP",
            intrabar_trailing_ambiguous=True,
        )
    if policy == "OPTIMISTIC":
        if hit_old_stop:
            # Peak updates first, then low touches new_stop
            return AmbiguityResolution(
                should_exit=True,
                exit_price=new_stop,
                exit_reason="TRAIL",
                new_peak_price=fav_extreme,
                new_trail_stop=new_stop,
                ambiguity_type="TRAIL_PEAK_AND_STOP",
                intrabar_trailing_ambiguous=True,
            )
        # Low did not touch old_stop -> stays open with updated peak & new_stop
        return AmbiguityResolution(
            should_exit=False,
            new_peak_price=fav_extreme,
            new_trail_stop=new_stop,
            ambiguity_type="TRAIL_PEAK_AND_STOP",
            intrabar_trailing_ambiguous=True,
        )
    # OHLC_PATH
    if _favorable_extreme_first(is_buy, bar_open, bar_close):
        # Favorable extreme updates peak first, then adverse extreme hits new_stop
        return AmbiguityResolution(
            should_exit=True,
            exit_price=new_stop,
            exit_reason="TRAIL",
            new_peak_price=fav_extreme,
            new_trail_stop=new_stop,
            ambiguity_type="TRAIL_PEAK_AND_STOP",
            intrabar_trailing_ambiguous=True,
        )
    # Adverse extreme happens first
    if hit_old_stop:
        return AmbiguityResolution(
            should_exit=True,
            exit_price=old_stop,
            exit_reason="TRAIL",
            new_peak_price=fav_extreme,
            new_trail_stop=old_stop,
            ambiguity_type="TRAIL_PEAK_AND_STOP",
            intrabar_trailing_ambiguous=True,
        )
    return AmbiguityResolution(
        should_exit=False,
        new_peak_price=fav_extreme,
        new_trail_stop=new_stop,
        ambiguity_type="TRAIL_PEAK_AND_STOP",
        intrabar_trailing_ambiguous=True,
    )
