"""Lab 10 L2 signal module for live (V3_LIVE_23_LAB_11_V1).

Hand-maintained since the pruning PR: unused incremental/live-state, annotation and
registry-extension code was removed; logic was kept as-is and proven identical by a
parity capture (synthetic seeds 7/99 and stress 6000) against the previous module.

Upgrading the lab later: diff the old and new lab code, apply only that difference here,
run the parity capture and the live tests, then open a PR.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Collection
from dataclasses import asdict, dataclass
from dataclasses import field
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Literal
from typing import Protocol
from typing import Sequence
import copy
import dataclasses
import hashlib
import itertools
import json
import logging
import math
import numpy as np
import pandas as pd
from autotrade_v3.l2_core import (
    OBConfig,
    _aroon,
    _clean_bool,
    _cmo,
    _confirmed_pivots,
    _cross_down,
    _cross_up,
    _demarker,
    _ichimoku,
    _kama_er,
    _nadaraya,
    _new6_raw_signals,
    _vortex,
    _want_combo,
    _want_variant,
    align_confirmed,
    approved,
    atr,
    cci,
    crossover,
    crossunder,
    dmi,
    edge_event,
    ema,
    ensure_dt,
    entry_variants,
    mfi,
    ob_context,
    obv_variant,
    parabolic_sar,
    pine_combo15_supertrend_dir,
    pine_custom_supertrend_dir,
    precompute,
    resample_ohlcv,
    rma,
    rsi,
    session_vwap,
    sma,
    smc_direction,
    stochastic,
    true_range,
)  # shared with live l2_core (identical bodies)
from autotrade_v3.live.l2_filters import (  # ETH C3/C6/C7/C12-C15 filters and lookup helpers
    C12V3Atr065NoExt24hFilter,
    C12_COMBO_ID,
    C13B1VolRegimeFilter,
    C13_COMBO_ID,
    BtcC1H2Atr100Filter,
    BtcC4B1VolConfirmFilter,
    BtcC7B1NoChase1HFilter,
    BTC_C1_COMBO_ID,
    BTC_C1_SYMBOL,
    BTC_C4_COMBO_ID,
    BTC_C4_SYMBOL,
    BTC_C7_COMBO_ID,
    BTC_C7_SYMBOL,
    C13_MAX_ATR_RATIO_50,
    C13_SYMBOL,
    C14H3aKumoBandFilter,
    C15V5SellAtr140Filter,
    C3B2NotOverextended4hFilter,
    C3_COMBO_ID,
    C3_MAX_EMA_TREND_4H_DIR,
    C3_SYMBOL,
    C6R2V4CombinedFilter,
    C6_COMBO_ID,
    C6_SYMBOL,
    C7R2V2AsymFilter,
    C7_COMBO_ID,
    FilterContext,
    H1_MS,
    H4_MS,
    build_c12_1h_features_lookup,
    build_c6_atr_ratio_lookup,
    build_c7_features_lookup,
    datetime_index_to_ms,
    generate_c6_cci_audit_sell_signals,
)
from autotrade_v3.live.lab10_core_params import EMBEDDED_LAB10_FIXED_PARAMS, EMBEDDED_LAB10_CORE_PARAMS


# --- from autotrade_v3/strategy_core/api.py
@dataclass(frozen=True)
class ExitPolicy__api:
    """Exit policy defining TP, SL, and Trailing parameters for a combo."""
    tp_pct: float
    sl_pct: float
    split_legs: bool = True
    trailing_act_pct: float = 0.005
    trailing_cb_pct: float = 0.005
    leg_weights: tuple[float, ...] = (0.5, 0.5)
    max_bars_held: int | None = None
    force_close_on_window_end: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.leg_weights, tuple):
            object.__setattr__(self, 'leg_weights', tuple((float(w) for w in self.leg_weights)))

    def get_policy_hash(self) -> str:
        """Deterministic SHA256 of the ExitPolicy parameters."""
        data = asdict(self)
        raw = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['policy_hash'] = self.get_policy_hash()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExitPolicy__api:
        return cls(tp_pct=float(data['tp_pct']), sl_pct=float(data['sl_pct']), split_legs=bool(data.get('split_legs', True)), trailing_act_pct=float(data.get('trailing_act_pct', 0.005)), trailing_cb_pct=float(data.get('trailing_cb_pct', 0.005)), leg_weights=tuple((float(x) for x in data.get('leg_weights', (0.5, 0.5)))), max_bars_held=int(data['max_bars_held']) if data.get('max_bars_held') is not None else None, force_close_on_window_end=bool(data.get('force_close_on_window_end', False)))

# --- from autotrade_v3/strategy_core/api.py
@dataclass(frozen=True)
class ComboSpec:
    """Full parameter specification for a single strategy combo."""
    combo_key: str
    symbol: str
    combo_id: int
    combo_name: str
    native_timeframe: str
    entry_variant: str
    entry: dict[str, Any]
    layer2: str
    exit_policy: ExitPolicy__api
    is_new6: bool = False

    def to_dict(self) -> dict[str, Any]:
        ep_dict = asdict(self.exit_policy)
        ep_dict['leg_weights'] = list(self.exit_policy.leg_weights)
        return {'combo_key': self.combo_key, 'symbol': self.symbol, 'combo_id': self.combo_id, 'combo_name': self.combo_name, 'native_timeframe': self.native_timeframe, 'is_new6': self.is_new6, 'entry_variant': self.entry_variant, 'entry': dict(self.entry), 'layer2': self.layer2, 'exit_policy': ep_dict}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComboSpec:
        cid = int(data['combo_id'])
        return cls(combo_key=str(data['combo_key']), symbol=str(data['symbol']), combo_id=cid, combo_name=str(data['combo_name']), native_timeframe=str(data['native_timeframe']), entry_variant=str(data['entry_variant']), entry=dict(data.get('entry', {})), layer2=str(data['layer2']), exit_policy=ExitPolicy__api.from_dict(data['exit_policy']), is_new6=bool(data.get('is_new6', cid >= 101)))

# --- from autotrade_v3/strategy_core/api.py
@dataclass(frozen=True)
class SignalEvent:
    """Canonical signal event emitted by Strategy.generate() and Strategy.on_bar_close().

    Contains no tp_price or sl_price; exit levels belong strictly to ExitPolicy/Simulator.
    """
    signal_id: str
    combo_key: str
    symbol: str
    side: Literal['BUY', 'SELL']
    native_open_ms: int
    signal_close_ms: int
    ref_price: float
    tags: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.signal_id:
            sym_clean = self.symbol.replace('-', '').replace('_', '')
            cname = self.tags.get('combo_name') or self.combo_key.split('/')[-1]
            tf = self.tags.get('timeframe', '15m')
            object.__setattr__(self, 'signal_id', f'SIG_{sym_clean}_{cname}_{tf}_{self.side}_{self.signal_close_ms}')

    def to_dict(self) -> dict[str, Any]:
        return {'signal_id': self.signal_id, 'combo_key': self.combo_key, 'symbol': self.symbol, 'side': self.side, 'native_open_ms': self.native_open_ms, 'signal_close_ms': self.signal_close_ms, 'ref_price': self.ref_price, 'tags': dict(self.tags)}

# --- from autotrade_v3/strategy_core/api.py
@dataclass(frozen=True)
class StrategySpec:
    """Immutable specification of a versioned strategy and its three cryptographic hashes."""
    strategy_id: str
    version: str
    combos: tuple[ComboSpec, ...]
    timeframes: tuple[str, ...]
    warmup_bars: int
    signal_spec_hash: str
    exit_spec_hash: str
    spec_hash: str
    parent: str | None = None


    def to_dict(self) -> dict[str, Any]:
        return {'strategy_id': self.strategy_id, 'version': self.version, 'parent': self.parent, 'warmup_bars': self.warmup_bars, 'timeframes': list(self.timeframes), 'signal_spec_hash': self.signal_spec_hash, 'exit_spec_hash': self.exit_spec_hash, 'spec_hash': self.spec_hash, 'combos': [c.to_dict() for c in self.combos]}

# --- from autotrade_v3/strategy_core/api.py
class Strategy(ABC):
    """Plugin interface for all strategies in AutoTrade V3 Strategy Core."""

    @abstractmethod
    def spec(self) -> StrategySpec:
        """Return the immutable StrategySpec (combos, timeframes, warmup_bars, hashes)."""

    @abstractmethod
    def generate(self, bars: dict[str, pd.DataFrame] | pd.DataFrame, symbols: list[str] | None=None, only_approved: bool=True, only_warmup_ready: bool=True) -> list[SignalEvent]:
        """Batch signal generation over closed M15 candles per symbol."""


# --- from autotrade_v3/strategy_core/hashing.py
STRATEGY_CORE_ROOT = Path(__file__).resolve().parent

# --- from autotrade_v3/strategy_core/hashing.py
def normalize_lf(text: str) -> str:
    """Normalize CRLF and CR line endings to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")

# --- from autotrade_v3/strategy_core/hashing.py
def canonical_json_bytes(obj: Any) -> bytes:
    """Serialize object to canonical UTF-8 JSON bytes with sorted keys and LF line endings."""
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return normalize_lf(raw).encode("utf-8")

# --- from autotrade_v3/strategy_core/hashing.py
def canonical_json_sha256(obj: Any) -> str:
    """Compute SHA256 hex digest over canonical JSON representation."""
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()

# --- from autotrade_v3/strategy_core/hashing.py
def collect_strategy_core_source_hashes(
    core_root: Path | None = None,
    strategy_subdir: str | None = "strategies/final20",
) -> dict[str, str]:
    """Hash core shared .py files + indicators/ + the specific strategy's own .py files (LF-normalized).

    Does NOT hash unrelated sibling strategy plugins under strategies/<other>/ so adding a new
    independent plugin strategy does not alter 20FINAL@1's signal_spec_hash.
    """
    root = Path(core_root).resolve() if core_root is not None else STRATEGY_CORE_ROOT

    selected_files: list[Path] = []
    # 1. Shared core modules at strategy_core root
    for pf in root.glob("*.py"):
        if "__pycache__" not in pf.parts:
            selected_files.append(pf)

    # 2. Shared indicator library
    ind_dir = root / "indicators"
    if ind_dir.exists():
        for pf in ind_dir.rglob("*.py"):
            if "__pycache__" not in pf.parts:
                selected_files.append(pf)

    # 3. Strategies package init + target strategy subdir
    strat_init = root / "strategies" / "__init__.py"
    if strat_init.exists():
        selected_files.append(strat_init)

    if strategy_subdir:
        strat_dir = root / strategy_subdir
        if strat_dir.exists():
            for pf in strat_dir.rglob("*.py"):
                if "__pycache__" not in pf.parts:
                    selected_files.append(pf)

    py_files = sorted(set(selected_files), key=lambda p: p.relative_to(root).as_posix())
    out: dict[str, str] = {}
    for pf in py_files:
        rel = pf.relative_to(root).as_posix()
        norm_text = normalize_lf(pf.read_text(encoding="utf-8"))
        out[rel] = hashlib.sha256(norm_text.encode("utf-8")).hexdigest()
    return out

# --- from autotrade_v3/strategy_core/hashing.py
def compute_signal_spec_hash(
    params_data: dict[str, Any],
    core_root: Path | None = None,
    strategy_subdir: str | None = "strategies/final20",
) -> str:
    """Compute signal_spec_hash from strategy_core .py sources and signal-affecting parameters."""
    eff_subdir = str(params_data.get("strategy_subdir", strategy_subdir)) if strategy_subdir is not None else None
    source_hashes = collect_strategy_core_source_hashes(core_root=core_root, strategy_subdir=eff_subdir)
    warmup = int(params_data.get("warmup_bars", 3400))

    raw_combos = params_data.get("combos", [])
    sorted_combos = sorted(
        raw_combos,
        key=lambda c: (str(c.get("symbol", "")), int(c.get("combo_id", 0)), str(c.get("combo_key", ""))),
    )

    signal_combos = []
    for c in sorted_combos:
        cid = int(c["combo_id"])
        signal_combos.append({
            "combo_key": str(c["combo_key"]),
            "symbol": str(c["symbol"]),
            "combo_id": cid,
            "combo_name": str(c.get("combo_name", "")),
            "native_timeframe": str(c["native_timeframe"]),
            "is_new6": bool(c.get("is_new6", cid >= 101)),
            "entry_variant": str(c["entry_variant"]),
            "entry": c["entry"],
            "layer2": str(c["layer2"]),
        })

    payload = {
        "source_files": source_hashes,
        "warmup": warmup,
        "signal_combos": signal_combos,
    }
    return canonical_json_sha256(payload)

# --- from autotrade_v3/strategy_core/hashing.py
def compute_exit_spec_hash(params_data: dict[str, Any]) -> str:
    """Compute exit_spec_hash from exit_policy of all combos."""
    raw_combos = params_data.get("combos", [])
    sorted_combos = sorted(
        raw_combos,
        key=lambda c: (str(c.get("symbol", "")), int(c.get("combo_id", 0)), str(c.get("combo_key", ""))),
    )

    exit_combos = []
    for c in sorted_combos:
        ep = c["exit_policy"]
        exit_combos.append({
            "combo_key": str(c["combo_key"]),
            "symbol": str(c["symbol"]),
            "combo_id": int(c["combo_id"]),
            "exit_policy": {
                "tp_pct": float(ep["tp_pct"]),
                "sl_pct": float(ep["sl_pct"]),
                "split_legs": bool(ep["split_legs"]),
                "leg_weights": [float(w) for w in ep["leg_weights"]],
                "trailing_act_pct": float(ep["trailing_act_pct"]),
                "trailing_cb_pct": float(ep["trailing_cb_pct"]),
                "max_bars_held": int(ep["max_bars_held"]) if ep.get("max_bars_held") is not None else None,
                "force_close_on_window_end": bool(ep.get("force_close_on_window_end", False)),
            },
        })

    return canonical_json_sha256({"exit_combos": exit_combos})

# --- from autotrade_v3/strategy_core/hashing.py
def compute_spec_hash(
    signal_spec_hash: str,
    exit_spec_hash: str,
    strategy_id: str,
    version: str,
) -> str:
    """Compute overall spec_hash combining signal_spec_hash, exit_spec_hash, strategy_id, and version."""
    payload = {
        "strategy_id": str(strategy_id),
        "version": str(version),
        "signal_spec_hash": str(signal_spec_hash),
        "exit_spec_hash": str(exit_spec_hash),
    }
    return canonical_json_sha256(payload)

# --- from autotrade_v3/strategy_core/hashing.py
def compute_all_hashes(
    params_data: dict[str, Any],
    core_root: Path | None = None,
) -> dict[str, str]:
    """Compute all three hashes (signal_spec_hash, exit_spec_hash, spec_hash) for a strategy spec."""
    strategy_id = str(params_data.get("strategy_id", "20FINAL"))
    version = str(params_data.get("version", "1"))
    sig_h = compute_signal_spec_hash(params_data, core_root=core_root)
    exit_h = compute_exit_spec_hash(params_data)
    spec_h = compute_spec_hash(sig_h, exit_h, strategy_id=strategy_id, version=version)
    return {
        "signal_spec_hash": sig_h,
        "exit_spec_hash": exit_h,
        "spec_hash": spec_h,
    }

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/indicators.py

# --- from autotrade_v3/strategy_core/indicators/signals.py

# --- from autotrade_v3/strategy_core/indicators/signals.py

# --- from autotrade_v3/strategy_core/indicators/signals.py

# --- from autotrade_v3/strategy_core/indicators/layer3_long.py

# --- from autotrade_v3/strategy_core/indicators/layer3_long.py

# --- from autotrade_v3/strategy_core/indicators/layer3_long.py

# --- from autotrade_v3/strategy_core/indicators/layer3_long.py

# --- from autotrade_v3/strategy_core/indicators/layer3_long.py

# --- from autotrade_v3/strategy_core/indicators/smc_structure.py

# --- from autotrade_v3/strategy_core/indicators/smc_ob.py

# --- from autotrade_v3/strategy_core/indicators/smc_ob.py

# --- from autotrade_v3/strategy_core/indicators/smc_ob.py

# --- from autotrade_v3/strategy_core/indicators/two_trail_layer12.py

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
MIN_LIVE_15M_BARS = 3400

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
M15_MS = 900000

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
LAB10_PARAMS_DIR = Path(__file__).resolve().parent / "lab10_params"

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
DEFAULT_PARAMS_PATH = LAB10_PARAMS_DIR / 'params' / '20final_v1.json'

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
DEFAULT_LOCK_PATH = LAB10_PARAMS_DIR / 'params' / '20final_v1.lock'

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
_BASELINE_ENTRY_DEFAULTS: dict[tuple[int, str], dict[str, Any]] = {(1, 'MFI_L50_S50'): {'mfi_long': 50.0, 'mfi_short': 50.0}, (2, 'ADX_20'): {'adx': 20.0}, (3, 'ADX_34'): {'adx': 34.0}, (4, 'ADX_23'): {'adx': 23.0}, (6, 'BODY_1.4'): {'body_atr': 1.4}, (6, 'BODY_1.8'): {'body_atr': 1.8}, (7, 'BODY1.6_VOL2.0'): {'body_atr': 1.6, 'volume_mult': 2.0}, (7, 'BODY1.3_VOL1.5'): {'body_atr': 1.3, 'volume_mult': 1.5}, (9, 'RSI45_FIXST'): {'rsi_long_max': 45.0, 'rsi_short_min': 55.0, 'fix_short_supertrend': True}, (10, 'ADX_22'): {'adx': 22.0}, (10, 'ADX_28'): {'adx': 28.0}, (11, 'ADX30_T27_T35_V1.3'): {'adx': 30.0, 'tier2_score': 7.0, 'tier3_score': 5.0, 'volume_mult': 1.3}, (101, 'trend_n60_0.25_0.75'): {'indicator': 'DeMarker', 'period': 60, 'short_max': 0.25, 'long_min': 0.75}, (102, 'cross_n40_t70'): {'indicator': 'Aroon', 'period': 40, 'threshold': 70.0}, (103, 'momcross_n40_t40'): {'indicator': 'CMO', 'period': 40, 'threshold': 40.0}, (104, 'tkcross_12_30_60'): {'indicator': 'Ichimoku', 'conv': 12, 'base': 30, 'spanb': 60}, (105, 'state_e40_s40_t0.4'): {'indicator': 'KAMA_ER', 'er_len': 40, 'fast_len': 2, 'slow_len': 40, 'er_min': 0.4}, (106, 'cross_n14_r1.2'): {'indicator': 'Vortex', 'period': 14, 'ratio': 1.2}}

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
def _is_default_entry(combo: ComboSpec) -> bool:
    expected = _BASELINE_ENTRY_DEFAULTS.get((combo.combo_id, combo.entry_variant))
    return expected is not None and combo.entry == expected

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
def _custom_final14_signals(pc: dict[str, Any], combo: ComboSpec) -> tuple[pd.Series, pd.Series]:
    """Evaluate FINAL14 entry signals when a clone modifies entry thresholds outside the default grid."""
    cid = combo.combo_id
    ent = combo.entry
    d15 = pc['d15']
    d1 = pc['d1']
    d4 = pc['d4']
    d6 = pc['d6']
    p15 = pc['15']
    p1 = pc['1']
    if cid in (1, 2, 3, 4, 6, 11):
        d = d1
        A21 = p1['A21']
        R = p1['R']
        M = p1['M']
        ADX = p1['ADX']
        K = p1['K']
        D = p1['D']
        C = p1['C']
        macd = ema(d.close, 8) - ema(d.close, 21)
        sig = ema(macd, 9)
        hist = macd - sig
        volma = p1['volma']
        volsp = d.volume > volma * 1.5
        adxr = ADX > ADX.shift(1)
        sb08 = (d.close > d.open) & (d.close - d.open > A21 * 0.8)
        ss08 = (d.close < d.open) & (d.open - d.close > A21 * 0.8)
        tu = d.close > p1['ema1504']
        td = d.close < p1['ema1504']
        longc = (K > 30) & (K > D) & (C > 80) & (C > C.shift(1))
        shortc = (K < 70) & (K < D) & (C > -80) & (C < C.shift(1))
        if cid == 1:
            lth = float(ent.get('mfi_long', 50.0))
            sth = float(ent.get('mfi_short', 50.0))
            L = longc & (R > 55) & (hist > 0) & (M > lth) & adxr & sb08 & tu & volsp
            S = shortc & (R < 45) & (hist < 0) & (M < sth) & adxr & ss08 & td & volsp
            return (edge_event(L), edge_event(S))
        if cid == 2:
            adxth = float(ent.get('adx', 20.0))
            e10 = ema(d.close, 10)
            e25 = ema(d.close, 25)
            basis = sma(d.close, 20)
            dev = 1.8 * d.close.rolling(20, min_periods=20).std(ddof=0)
            bbup = d.close > basis + dev
            bbdn = d.close < basis - dev
            a14 = p1.get('A14') if 'A14' in p1 else atr(d, 14)
            trstop = 1.5 * a14
            x = d.close.to_numpy(float)
            ts = trstop.to_numpy(float)
            arr = np.full(len(d), np.nan)
            for i in range(len(d)):
                prev = arr[i - 1] if i else np.nan
                nz = 0 if not np.isfinite(prev) else prev
                prevsrc = x[i - 1] if i else np.nan
                if i and x[i] > nz and (prevsrc > nz):
                    arr[i] = max(nz, x[i] - ts[i])
                elif i and x[i] < nz and (prevsrc < nz):
                    arr[i] = min(nz, x[i] + ts[i])
                else:
                    arr[i] = x[i] - ts[i] if x[i] > nz else x[i] + ts[i]
            ats = pd.Series(arr, index=d.index)
            above = crossover(ema(d.close, 1), ats)
            below = crossover(ats, ema(d.close, 1))
            L = (d.close > ats) & above & (e10 > e25) & (macd > sig) & (ADX > adxth) & bbup
            S = (d.close < ats) & below & (e10 < e25) & (macd < sig) & (ADX > adxth) & bbdn
            return (edge_event(L), edge_event(S))
        stdir1 = pine_custom_supertrend_dir(d, 8, 4.0)
        if cid == 3:
            adxth = float(ent.get('adx', 34.0))
            sar = parabolic_sar(d, 0.05, 0.2, 0.3)
            bull_eng = (d.close > d.open) & (d.close.shift(1) < d.open.shift(1)) & (d.close > d.open.shift(1)) & (d.open < d.close.shift(1))
            bear_eng = (d.close < d.open) & (d.close.shift(1) > d.open.shift(1)) & (d.close < d.open.shift(1)) & (d.open > d.close.shift(1))
            O = obv_variant(d)
            oup = O > O.shift(1)
            odn = O < O.shift(1)
            L = (stdir1 == 1) & (ADX > adxth) & (d.close > sar) & bull_eng & oup
            S = (stdir1 == -1) & (ADX > adxth) & (d.close < sar) & bear_eng & odn
            return (edge_event(L), edge_event(S))
        e50_6 = align_confirmed(ema(d6.close, 50), d.index, '6h')
        tfast = ema(d.close, 21)
        tslow = ema(d.close, 42)
        bt = tfast > tslow
        br = tfast < tslow
        std20 = d.close.rolling(20, min_periods=20).std(ddof=0)
        bbu = sma(d.close, 20) + 2 * std20
        bbl = sma(d.close, 20) - 2 * std20
        ku = sma(d.close, 20) + 1.7 * atr(d, 20)
        kl = sma(d.close, 20) - 1.7 * atr(d, 20)
        breakout_b = crossover(d.close, bbu) & (d.close > ku)
        breakout_s = crossunder(d.close, bbl) & (d.close < kl)
        if cid == 4:
            adxth = float(ent.get('adx', 23.0))
            L = bt & breakout_b & (d.close > e50_6) & (stdir1 == 1) & sb08 & volsp & (ADX > adxth)
            S = br & breakout_s & (d.close < e50_6) & (stdir1 == -1) & ss08 & volsp & (ADX > adxth)
            return (edge_event(L), edge_event(S))
        if cid == 6:
            bm = float(ent.get('body_atr', 1.4))
            lc6 = (R > 30) & (K > 30) & (K > D) & (C > 100) & (C > C.shift(1))
            sc6 = (R < 70) & (K < 70) & (K < D) & (C > -100) & (C < C.shift(1))
            adxs = (ADX > 20) & adxr
            sb = (d.close > d.open) & (d.close - d.open > A21 * bm)
            ss = (d.close < d.open) & (d.open - d.close > A21 * bm)
            return (edge_event(sb & breakout_b & lc6 & adxs), edge_event(ss & breakout_s & sc6 & adxs))
        if cid == 11:
            adxth = float(ent.get('adx', 30.0))
            t2 = float(ent.get('tier2_score', 7.0))
            t3 = float(ent.get('tier3_score', 5.0))
            vm = float(ent.get('volume_mult', 1.3))
            dip = p1['dip']
            dim = p1['dim']
            A20 = atr(d, 20)
            e50 = align_confirmed(ema(d4.close, 50), d.index, '4h')
            e150 = align_confirmed(ema(d4.close, 150), d.index, '4h')
            e200 = align_confirmed(ema(d4.close, 200), d.index, '4h')
            V = session_vwap(d)
            macd12 = ema(d.close, 12) - ema(d.close, 26)
            sig12 = ema(macd12, 9)
            hist12 = macd12 - sig12
            C9 = cci(d.close, d.high, d.low, 9)
            K14 = stochastic(d.close, d.high, d.low, 14)
            D14 = sma(K14, 3)
            e9 = ema(d.close, 9)
            e21 = ema(d.close, 21)
            sup = sma(d.close, 8) + 2 * A20
            strong = (d.close - d.open).abs() > A20 * 0.8
            sd = d.close.rolling(20, min_periods=20).std(ddof=0)
            z = (d.close - sma(d.close, 20)) / sd
            ao = ema(d.close, 5) - ema(d.close, 34)
            sq = ema(d.close, 20) - ema(d.close, 50)
            rav = sma(R, 14)
            stdt = pine_custom_supertrend_dir(d, 8, 4.0)
            pcL = d.close > d.close.rolling(20, min_periods=20).max().shift(1)
            pcS = d.close < d.close.rolling(20, min_periods=20).min().shift(1)
            vs = d.volume > sma(d.volume, 20) * vm
            tdL = (dip > dim) & (dip > 22)
            tdS = (dip < dim) & (dim > 22)
            trL = (e50 > e200) & tdL & (d.close > V) & (e50 > e50.shift(1))
            trS = (e50 < e200) & tdS & (d.close < V) & (e50 < e50.shift(1))
            t1L = trL & vs & (ADX > adxth)
            t1S = trS & vs & (ADX > adxth)
            s2L = (hist12 > 0) * 2 + (e150 > e200) * 2 + (C9 > 100) * 1 + (R > 50) * 1 + (d.close > sup) * 1 + crossover(K14, D14) * 0.5 + (d.close > e21) * 1 + (strong & (d.close > d.open)) * 1
            s2S = (hist12 < 0) * 2 + (e150 < e200) * 2 + (C9 < -100) * 1 + (R < 50) * 1 + (d.close < sup) * 1 + crossunder(K14, D14) * 0.5 + (d.close < e21) * 1 + (strong & (d.close < d.open)) * 1
            s2L = s2L + (M > 55) * 1 + (A20 > A20.shift(1)) * 1 + (e9 > e21) * 1 + ((C9 > C9.shift(1)) & (C9 > 100)) * 1 + ((K14 > K14.shift(1)) & (K14 > 50)) * 1
            s2S = s2S + (M < 45) * 1 + (A20 > A20.shift(1)) * 1 + (e9 < e21) * 1 + ((C9 < C9.shift(1)) & (C9 < -100)) * 1 + ((K14 < K14.shift(1)) & (K14 < 50)) * 1
            s3L = (z < -1.5) * 1 + (ao > 0) * 1 + (sq > 0) * 1 + (R > rav) * 1 + (e9 > e21) * 1 + (hist12 > 0) * 1 + (stdt == 1) * 1
            s3S = (z > 1.5) * 1 + (ao < 0) * 1 + (sq < 0) * 1 + (R < rav) * 1 + (e9 < e21) * 1 + (hist12 < 0) * 1 + (stdt == -1) * 1
            vma = sma(d.volume, 20)
            vsp = d.volume > vma * vm
            mrL = (M > 60) & (M > M.shift(1)) & vsp
            mrS = (M < 40) & (M < M.shift(1)) & vsp
            s3L = s3L + pcL * 1 + mrL * 1 + (R > 50) * 1 + vsp * 1
            s3S = s3S + pcS * 1 + mrS * 1 + (R < 50) * 1 + vsp * 1
            return (edge_event(t1L & (s2L >= t2) & (s3L >= t3)), edge_event(t1S & (s2S >= t2) & (s3S >= t3)))
    d = d15
    A = p15['A']
    R = p15['R']
    M = p15['M']
    ADX = p15['ADX']
    volSMA = p15['volSMA']
    body_ratio = p15['body_ratio']
    valid = body_ratio > 0.75
    if cid == 7:
        bm = float(ent.get('body_atr', 1.6))
        vm = float(ent.get('volume_mult', 2.0))
        ef = ema(d.close, 21)
        es = ema(d.close, 55)
        adxr = ADX > ADX.shift(1)
        volume_surge = d.volume > d.volume.shift(1) * 1.8
        tu = d.close > p15['trend100']
        td = d.close < p15['trend100']
        strong_b = (d.close > d.open) & (d.close - d.open > A * bm)
        strong_s = (d.close < d.open) & (d.open - d.close > A * bm)
        volsp = d.volume > volSMA * vm
        L = volume_surge & (ef > es) & tu & adxr & (ADX > 25) & strong_b & volsp & valid & (R > 60) & (R < 85)
        S = volume_surge & (ef < es) & td & adxr & (ADX > 25) & strong_s & volsp & valid & (R < 40) & (R > 15)
        return (edge_event(L), edge_event(S))
    if cid == 9:
        r_long = float(ent.get('rsi_long_max', 45.0))
        r_short = float(ent.get('rsi_short_min', 100.0 - r_long))
        fix_st = bool(ent.get('fix_short_supertrend', True))
        ema200 = p15['ema200']
        stdir15 = p15['stdir']
        mid = _nadaraya(d.close, 24, 3.0)
        nr = d.close.rolling(24, min_periods=24).std(ddof=0) * 2.3
        upper = mid + nr
        lower = mid - nr
        L = (d.close > ema200) & (R < r_long) & (d.close < lower) & (stdir15 == 1) & (body_ratio > 0.4)
        st_cond = stdir15 == -1 if fix_st else stdir15 == 1
        S = (d.close < ema200) & (R > r_short) & (d.close > upper) & st_cond & (body_ratio > 0.4)
        return (edge_event(L), edge_event(S))
    if cid == 10:
        adxth = float(ent.get('adx', 22.0))
        ao = sma((d.high + d.low) / 2, 5) - sma((d.high + d.low) / 2, 34)
        squeeze = ema(d.close, 20) - ema(d.close, 50)
        mean = sma(d.close, 20)
        sd = d.close.rolling(20, min_periods=20).std(ddof=0)
        z = (d.close - mean) / sd
        tu4 = d.close > p15['trend100']
        td4 = d.close < p15['trend100']
        upper_w = d.high - pd.concat([d.close, d.open], axis=1).max(axis=1)
        lower_w = pd.concat([d.close, d.open], axis=1).min(axis=1) - d.low
        body = (d.close - d.open).abs()
        bull_pin = (lower_w > body * 1.5) & (d.close > d.open)
        bear_pin = (upper_w > body * 1.5) & (d.close < d.open)
        L = (ao > 0) & (squeeze > 0) & (z < -1.5) & tu4 & (ADX > adxth) & bull_pin
        S = (ao < 0) & (squeeze < 0) & (z > 1.5) & td4 & (ADX > adxth) & bear_pin
        return (edge_event(L), edge_event(S))
    raise KeyError(f'Unsupported custom FINAL14 combo {combo.combo_key}')

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
def _custom_new6_raw_signals(d1: pd.DataFrame, combo: ComboSpec) -> tuple[pd.Series, pd.Series]:
    """Evaluate NEW6 raw signals when a clone modifies entry parameters."""
    if _is_default_entry(combo):
        return _new6_raw_signals(d1, combo.combo_id)
    h = d1.high.to_numpy(float)
    l = d1.low.to_numpy(float)
    c = d1.close.to_numpy(float)
    cid = int(combo.combo_id)
    ent = combo.entry
    if cid == 101:
        n = int(ent.get('period', 60))
        long_min = float(ent.get('long_min', 0.75))
        short_max = float(ent.get('short_max', 0.25))
        z = _demarker(h, l, n)
        return (_clean_bool(z > long_min, d1.index), _clean_bool(z < short_max, d1.index))
    if cid == 102:
        period = int(ent.get('period', 40))
        th = float(ent.get('threshold', 70.0))
        up, down = _aroon(h, l, period)
        return (_clean_bool(_cross_up(up, down) & (up >= th), d1.index), _clean_bool(_cross_up(down, up) & (down >= th), d1.index))
    if cid == 103:
        length = int(ent.get('period', 40))
        th = float(ent.get('threshold', 40.0))
        z = _cmo(c, length)
        long = (z > th) & np.r_[False, z[:-1] <= th]
        short = (z < -th) & np.r_[False, z[:-1] >= -th]
        return (_clean_bool(long, d1.index), _clean_bool(short, d1.index))
    if cid == 104:
        conv = int(ent.get('conv', 12))
        base = int(ent.get('base', 30))
        spanb = int(ent.get('spanb', 60))
        tenkan, kijun, span_a, span_b = _ichimoku(h, l, conv, base, spanb)
        top = np.maximum(span_a, span_b)
        bottom = np.minimum(span_a, span_b)
        return (_clean_bool(_cross_up(tenkan, kijun) & (c > top) & (span_a > span_b), d1.index), _clean_bool(_cross_down(tenkan, kijun) & (c < bottom) & (span_a < span_b), d1.index))
    if cid == 105:
        er_len = int(ent.get('er_len', 40))
        fast_len = int(ent.get('fast_len', 2))
        slow_len = int(ent.get('slow_len', 40))
        er_min = float(ent.get('er_min', 0.4))
        kama, er = _kama_er(c, er_len, fast_len, slow_len)
        slope = np.r_[np.nan, np.diff(kama)]
        return (_clean_bool((c > kama) & (slope > 0) & (er > er_min), d1.index), _clean_bool((c < kama) & (slope < 0) & (er > er_min), d1.index))
    if cid == 106:
        length = int(ent.get('period', 14))
        ratio = float(ent.get('ratio', 1.2))
        vi_plus, vi_minus = _vortex(h, l, c, length)
        return (_clean_bool(_cross_up(vi_plus, vi_minus) & (vi_plus > vi_minus * ratio), d1.index), _clean_bool(_cross_up(vi_minus, vi_plus) & (vi_minus > vi_plus * ratio), d1.index))
    raise KeyError(f'Unknown NEW6 combo {cid}')

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
def _make_signal_event(sym: str, combo: ComboSpec, side_code: str, ot: pd.Timestamp, pos: int, sd: int, blocked: bool, ok: bool, d: pd.DataFrame, warmup_bars: int) -> SignalEvent:
    native = combo.native_timeframe
    last_open = ot + (pd.Timedelta(minutes=45) if native == '1h' else pd.Timedelta(0))
    if d.index[pos] != last_open:
        raise AssertionError('Cannot publish signal before native candle close')
    close_ms = int(d.index[pos].value // 1000000 + M15_MS - 1)
    native_open_ms = int(ot.value // 1000000)
    bar_open_ms = int(d.index[pos].value // 1000000)
    side_str = 'BUY' if side_code == 'L' else 'SELL'
    sym_clean = sym.replace('-', '').replace('_', '')
    signal_id = f'SIG_{sym_clean}_{combo.combo_name}_{native}_{side_str}_{close_ms}'
    ref_price = float(d.iloc[pos]['close'])
    provider = str(d.iloc[pos].get('provider', 'UNKNOWN'))
    return SignalEvent(signal_id=signal_id, combo_key=combo.combo_key, symbol=sym, side=side_str, native_open_ms=native_open_ms, signal_close_ms=close_ms, ref_price=ref_price, tags={'smc_dir': int(sd), 'ob_blocked': bool(blocked), 'approved': bool(ok), 'warmup_ready': bool(pos >= warmup_bars - 1), 'combo_id': int(combo.combo_id), 'combo_name': combo.combo_name, 'timeframe': native, 'bar_position': int(pos), 'bar_open_ms': bar_open_ms, 'provider': provider, 'is_new6': bool(combo.is_new6)})

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class Lab10CoreStrategy(Strategy):
    """Lab 10 core: signal logic over the embedded 20FINAL@1 params (values frozen by hash)."""

    def __init__(self, params_data: dict[str, Any] | None=None, params_path: Path | None=None, lock_path: Path | None=None, core_root: Path | None=None, enforce_lock: bool=True):
        self._params_path = Path(params_path) if params_path is not None else DEFAULT_PARAMS_PATH
        self._lock_path = Path(lock_path) if lock_path is not None else DEFAULT_LOCK_PATH
        self._core_root = Path(core_root) if core_root is not None else LAB10_PARAMS_DIR.parents[1]
        if params_data is None:
            raw_text = normalize_lf(self._params_path.read_text(encoding='utf-8'))
            params_data = json.loads(raw_text)
        self._params_data = params_data
        strategy_id = str(params_data.get('strategy_id', '20FINAL'))
        version = str(params_data.get('version', '1'))
        warmup_bars = int(params_data.get('warmup_bars', MIN_LIVE_15M_BARS))
        timeframes = tuple((str(tf) for tf in params_data.get('timeframes', ['15m', '1h'])))
        parent = params_data.get('parent')
        hashes = compute_all_hashes(params_data, core_root=self._core_root)
        combos = tuple((ComboSpec.from_dict(c) for c in params_data.get('combos', [])))
        self._spec = StrategySpec(strategy_id=strategy_id, version=version, combos=combos, timeframes=timeframes, warmup_bars=warmup_bars, signal_spec_hash=hashes['signal_spec_hash'], exit_spec_hash=hashes['exit_spec_hash'], spec_hash=hashes['spec_hash'], parent=str(parent) if parent is not None else None)

    def spec(self) -> StrategySpec:
        return self._spec

    def _generate_symbol(self, sym: str, d_in: pd.DataFrame, only_approved: bool=True, only_warmup_ready: bool=True) -> list[SignalEvent]:
        if not isinstance(d_in.index, pd.DatetimeIndex) or d_in.index.tz is None:
            raise ValueError('Need timestamp-indexed UTC M15 candles')
        d = d_in.tz_convert('UTC')
        t = d.index.asi8 // 1000000
        warmup_bars = self._spec.warmup_bars
        if len(d) < warmup_bars or d.index.has_duplicates or (not d.index.is_monotonic_increasing) or (t % M15_MS).any() or (t[1:] - t[:-1] != M15_MS).any():
            raise ValueError('M15 gapped, misaligned or insufficient live warmup')
        sym_combos = [c for c in self._spec.combos if c.symbol.upper() == sym.upper()]
        if not sym_combos:
            return []
        final14_combos = sorted([c for c in sym_combos if not c.is_new6], key=lambda c: c.combo_id)
        new6_combos = sorted([c for c in sym_combos if c.is_new6], key=lambda c: c.combo_id)
        wanted: dict[str, set[str]] = {}
        for c in final14_combos:
            wanted.setdefault(c.combo_name, set()).add(c.entry_variant)
        pc = precompute(d, wanted if wanted else None)
        selected = entry_variants(pc, wanted) if wanted else {}
        d1 = pc['d1']
        smc15 = smc_direction(d, 50, False)
        smc1 = smc_direction(d1, 50, False)
        obcfg = OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5)
        ob15 = ob_context(d, obcfg)
        ob1 = ob_context(d1, obcfg)
        posmap = {ts: i for i, ts in enumerate(d.index)}
        events: list[SignalEvent] = []
        for c in final14_combos:
            native = c.native_timeframe
            if _is_default_entry(c):
                variants = [x for x in selected.get(c.combo_name, []) if x[0] == c.entry_variant]
                if len(variants) == 1:
                    _, L, S = variants[0]
                else:
                    L, S = _custom_final14_signals(pc, c)
            else:
                L, S = _custom_final14_signals(pc, c)
            rows = []
            for side_code, series in (('L', L), ('S', S)):
                for ot in series.index[series.fillna(False)]:
                    last = ot + (pd.Timedelta(minutes=45) if native == '1h' else pd.Timedelta(0))
                    if last not in posmap:
                        continue
                    sd = int((smc1 if native == '1h' else smc15).loc[ot])
                    ob = (ob1 if native == '1h' else ob15).loc[ot]
                    blocked = bool(ob['buy_blocked' if side_code == 'L' else 'sell_blocked'])
                    ok = bool(approved(side_code, sd, blocked, c.layer2))
                    rows.append((posmap[last], side_code, ot, sd, blocked, ok))
            rows.sort(key=lambda x: x[0])
            for p, side_code, ot, sd, blocked, ok in rows:
                ev = _make_signal_event(sym, c, side_code, ot, p, sd, blocked, ok, d, warmup_bars)
                if only_approved and (not ev.tags['approved']):
                    continue
                if only_warmup_ready and (not ev.tags['warmup_ready']):
                    continue
                events.append(ev)
        for c in new6_combos:
            L, S = _custom_new6_raw_signals(d1, c)
            for ot in d1.index:
                lng, shrt = (bool(L.loc[ot]), bool(S.loc[ot]))
                if lng == shrt:
                    continue
                last = ot + pd.Timedelta(minutes=45)
                if last not in posmap:
                    continue
                side_code = 'L' if lng else 'S'
                sd = int(smc1.loc[ot])
                blocked = bool(ob1.loc[ot, 'buy_blocked' if lng else 'sell_blocked'])
                ok = bool(approved(side_code, sd, blocked, c.layer2))
                ev = _make_signal_event(sym, c, side_code, ot, posmap[last], sd, blocked, ok, d, warmup_bars)
                if only_approved and (not ev.tags['approved']):
                    continue
                if only_warmup_ready and (not ev.tags['warmup_ready']):
                    continue
                events.append(ev)
        events.sort(key=lambda e: (e.signal_close_ms, int(e.tags['combo_id']), e.side))
        return events

    @staticmethod
    def _canonical_symbol(sym: str) -> str:
        up = sym.upper()
        if 'BTC' in up:
            return 'BTC-USDT'
        if 'ETH' in up:
            return 'ETH-USDT'
        return sym

    def generate(self, bars: dict[str, pd.DataFrame] | pd.DataFrame, symbols: list[str] | None=None, only_approved: bool=True, only_warmup_ready: bool=True) -> list[SignalEvent]:
        """Batch signal generation across provided closed M15 candle frames."""
        if isinstance(bars, pd.DataFrame):
            sym = symbols[0] if symbols else 'BTC-USDT'
            sym_canonical = self._canonical_symbol(sym)
            evs = self._generate_symbol(sym_canonical, bars, only_approved=only_approved, only_warmup_ready=only_warmup_ready)
            if sym != sym_canonical:
                evs = [SignalEvent(signal_id=e.signal_id, combo_key=e.combo_key, symbol=sym, side=e.side, native_open_ms=e.native_open_ms, signal_close_ms=e.signal_close_ms, ref_price=e.ref_price, tags=e.tags) for e in evs]
            return evs
        target_symbols = symbols or list(bars.keys())
        all_events: list[SignalEvent] = []
        for sym in target_symbols:
            if sym not in bars:
                continue
            sym_canonical = self._canonical_symbol(sym)
            evs = self._generate_symbol(sym_canonical, bars[sym], only_approved=only_approved, only_warmup_ready=only_warmup_ready)
            if sym != sym_canonical:
                evs = [SignalEvent(signal_id=e.signal_id, combo_key=e.combo_key, symbol=sym, side=e.side, native_open_ms=e.native_open_ms, signal_close_ms=e.signal_close_ms, ref_price=e.ref_price, tags=e.tags) for e in evs]
            all_events.extend(evs)
        all_events.sort(key=lambda e: (e.signal_close_ms, int(e.tags['combo_id']), e.side))
        return all_events



# --- from autotrade_v3/strategy_core/strategies/final20/logic.py

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
H6_MS = 21600000

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
DAY_MS = 86400000



























# --- from autotrade_v3/strategy_core/registry.py
PARAMS_DIR = LAB10_PARAMS_DIR / "params"

# --- from autotrade_v3/strategy_core/registry.py
def parse_strategy_ref(
    strategy_id_or_ref: str,
    version: str | None = None,
) -> tuple[str, str]:
    """Parse '20FINAL@1' or ('20FINAL', '1') into canonical (strategy_id, version)."""
    raw = str(strategy_id_or_ref).strip()
    if "@" in raw and version is None:
        sid, ver = raw.split("@", 1)
        sid = sid.strip()
        ver = ver.strip()
    else:
        sid = raw
        ver = str(version).strip() if version is not None else "1"

    if sid.upper() in ("LAB10_BASELINE", "20FINAL"):
        sid = "20FINAL"
        if ver in ("1.0.0", "1.0", ""):
            ver = "1"
    return sid, ver



# --- from autotrade_v3/strategy_core/registry.py
class StrategyCoreRegistry:
    """Registry indexing strategies by (strategy_id, version)."""

    _factories: dict[tuple[str, str], Callable[[], Strategy]] = {}
    _custom_instances: dict[tuple[str, str], Strategy] = {}


    @classmethod
    def get(
        cls,
        strategy_id_or_ref: str,
        version: str | None = None,
        params_dir: Path | None = None,
    ) -> Strategy:
        sid, ver = parse_strategy_ref(strategy_id_or_ref, version)
        key = (sid, ver)

        if key in cls._custom_instances:
            return cls._custom_instances[key]
        if key in cls._factories:
            return cls._factories[key]()

        if key == ("20FINAL_FIXED_V1", "1"):
            return Lab10CoreStrategy(
                params_data=copy.deepcopy(EMBEDDED_LAB10_FIXED_PARAMS),
                enforce_lock=False,
            )

        p_dir = Path(params_dir) if params_dir is not None else PARAMS_DIR
        if key == ("20FINAL", "1"):
            # Embedded copy of the frozen 20FINAL@1 params (lab10_core_params.py). The
            # params JSON and lock file are no longer read by live.
            return Lab10CoreStrategy(
                params_data=copy.deepcopy(EMBEDDED_LAB10_CORE_PARAMS),
                enforce_lock=False,
            )

        slug = f"{sid.lower()}_v{ver}"
        candidates = [
            p_dir / slug / "params.json",
            p_dir / f"{slug}.json",
            p_dir / f"{sid}_v{ver}.json",
        ]
        for cand in candidates:
            if cand.exists():
                return Lab10CoreStrategy(
                    params_path=cand,
                    enforce_lock=False,
                )

        raise KeyError(
            f"Strategy '{sid}@{ver}' not found in StrategyCoreRegistry. "
            f"Registered: {cls.list_keys(params_dir=p_dir)}"
        )


    @classmethod
    def list_keys(cls, params_dir: Path | None = None) -> list[str]:
        keys = {f"{sid}@{ver}" for sid, ver in cls._factories}
        keys |= {f"{sid}@{ver}" for sid, ver in cls._custom_instances}
        keys.add("20FINAL@1")
        p_dir = Path(params_dir) if params_dir is not None else PARAMS_DIR
        if p_dir.exists():
            for sub in sorted(p_dir.iterdir()):
                if sub.is_dir() and (sub / "params.json").exists():
                    try:
                        d = json.loads(normalize_lf((sub / "params.json").read_text(encoding="utf-8")))
                        keys.add(f"{d['strategy_id']}@{d['version']}")
                    except Exception:
                        pass
        return sorted(keys)



# --- from autotrade_v3/engine/signal_engine.py
@dataclass(frozen=True)
class Signal:
    symbol: str
    combo_id: int
    combo_name: str
    timeframe: str
    side: Literal["BUY", "SELL"]
    signal_close_ms: int
    signal_close_dt: pd.Timestamp
    bar_position: int
    bar_open_ms: int
    entry_price: float
    tp_pct: float
    sl_pct: float
    tp_price: float
    sl_price: float
    approved: bool
    warmup_ready: bool
    smc_dir: int
    ob_blocked: bool
    provider: str
    is_new6: bool
    exit_policy: Any = None
    exit_policy_hash: str = ""
    signal_id: str = ""

    def __post_init__(self) -> None:
        if not self.signal_id:
            sym_clean = self.symbol.replace("-", "").replace("_", "")
            object.__setattr__(
                self,
                "signal_id",
                f"SIG_{sym_clean}_{self.combo_name}_{self.timeframe}_{self.side}_{self.signal_close_ms}",
            )

# --- from autotrade_v3/engine/signal_engine.py
def signal_from_event(
    ev: SignalEvent,
    combo: ComboSpec,
    exit_policy_obj: Any = None,
    exit_policy_hash: str = "",
) -> Signal:
    """Convert a pure Strategy Core `SignalEvent` + `ComboSpec` into the 23-column `Signal`."""
    entry_px = float(ev.ref_price)
    tp_pct = float(combo.exit_policy.tp_pct)
    sl_pct = float(combo.exit_policy.sl_pct)
    if ev.side == "BUY":
        tp_px = entry_px * (1.0 + tp_pct)
        sl_px = entry_px * (1.0 - sl_pct)
    else:
        tp_px = entry_px * (1.0 - tp_pct)
        sl_px = entry_px * (1.0 + sl_pct)

    sig_dt = pd.to_datetime(ev.signal_close_ms, unit="ms", utc=True)
    tags = ev.tags
    return Signal(
        symbol=ev.symbol,
        combo_id=int(combo.combo_id),
        combo_name=combo.combo_name,
        timeframe=combo.native_timeframe,
        side=ev.side,
        signal_close_ms=int(ev.signal_close_ms),
        signal_close_dt=sig_dt,
        bar_position=int(tags.get("bar_position", 0)),
        bar_open_ms=int(tags.get("bar_open_ms", ev.native_open_ms)),
        entry_price=entry_px,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        tp_price=tp_px,
        sl_price=sl_px,
        approved=bool(tags.get("approved", True)),
        warmup_ready=bool(tags.get("warmup_ready", True)),
        smc_dir=int(tags.get("smc_dir", 0)),
        ob_blocked=bool(tags.get("ob_blocked", False)),
        provider=str(tags.get("provider", "UNKNOWN")),
        is_new6=bool(combo.is_new6),
        exit_policy=exit_policy_obj,
        exit_policy_hash=exit_policy_hash,
        signal_id=ev.signal_id,
    )

# --- from autotrade_v3/engine/signal_engine.py
class SignalEngine:
    """Wraps independent Strategy Core signal generation and attaches execution parameters."""

    def __init__(self, core_strategy: Strategy | None = None):
        self._core = core_strategy or StrategyCoreRegistry.get("20FINAL", "1")
        self._refresh_cases()

    def _refresh_cases(self) -> None:
        spec = self._core.spec()
        self.final14_cases: dict[str, dict[int, dict[str, Any]]] = {"BTC-USDT": {}, "ETH-USDT": {}}
        self.new6_cases: dict[str, dict[int, dict[str, Any]]] = {"BTC-USDT": {}, "ETH-USDT": {}}
        self._combo_by_sym_id: dict[tuple[str, int], ComboSpec] = {}
        self._combo_by_key: dict[str, ComboSpec] = {}

        for c in spec.combos:
            self._combo_by_sym_id[(c.symbol, c.combo_id)] = c
            self._combo_by_key[c.combo_key] = c
            cfg = {
                "symbol": c.symbol,
                "combo": c.combo_id,
                "name": c.combo_name,
                "native": c.native_timeframe,
                "entry_variant": c.entry_variant,
                "entry": dict(c.entry),
                "layer2": c.layer2,
                "tp_pct": c.exit_policy.tp_pct,
                "sl_pct": c.exit_policy.sl_pct,
                "is_new6": c.is_new6,
                "exit_policy": c.exit_policy.to_dict(),
            }
            if c.is_new6:
                self.new6_cases.setdefault(c.symbol, {})[c.combo_id] = cfg
            else:
                self.final14_cases.setdefault(c.symbol, {})[c.combo_id] = cfg

    @property
    def core_strategy(self) -> Strategy:
        return self._core

    def get_combo_spec(self, symbol: str, combo_id: int) -> ComboSpec:
        sym = "BTC-USDT" if "BTC" in symbol.upper() else ("ETH-USDT" if "ETH" in symbol.upper() else symbol)
        key = (sym, int(combo_id))
        if key not in self._combo_by_sym_id:
            raise KeyError(f"Combo {combo_id} not found in strategy config for {symbol}")
        return self._combo_by_sym_id[key]

    def get_combo_config(self, symbol: str, combo_id: int) -> dict[str, Any]:
        sym = "BTC-USDT" if "BTC" in symbol.upper() else ("ETH-USDT" if "ETH" in symbol.upper() else symbol)
        if combo_id in self.final14_cases.get(sym, {}):
            return dict(self.final14_cases[sym][combo_id])
        if combo_id in self.new6_cases.get(sym, {}):
            return dict(self.new6_cases[sym][combo_id])
        raise KeyError(f"Combo {combo_id} not found in strategy config for {symbol}")

    def generate_signals(
        self,
        symbol: str,
        df_m15: pd.DataFrame,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate signals via Strategy Core on M15 DataFrame and enrich with entry/TP/SL prices."""
        sym_canonical = "BTC-USDT" if "BTC" in symbol.upper() else ("ETH-USDT" if "ETH" in symbol.upper() else symbol)
        events = self._core.generate(
            {sym_canonical: df_m15},
            symbols=[sym_canonical],
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        signals: list[Signal] = []
        for ev in events:
            combo = self._combo_by_key[ev.combo_key]
            sig = signal_from_event(ev, combo)
            if symbol != sym_canonical:
                sig = Signal(
                    symbol=symbol,
                    combo_id=sig.combo_id,
                    combo_name=sig.combo_name,
                    timeframe=sig.timeframe,
                    side=sig.side,
                    signal_close_ms=sig.signal_close_ms,
                    signal_close_dt=sig.signal_close_dt,
                    bar_position=sig.bar_position,
                    bar_open_ms=sig.bar_open_ms,
                    entry_price=sig.entry_price,
                    tp_pct=sig.tp_pct,
                    sl_pct=sig.sl_pct,
                    tp_price=sig.tp_price,
                    sl_price=sig.sl_price,
                    approved=sig.approved,
                    warmup_ready=sig.warmup_ready,
                    smc_dir=sig.smc_dir,
                    ob_blocked=sig.ob_blocked,
                    provider=sig.provider,
                    is_new6=sig.is_new6,
                    exit_policy=sig.exit_policy,
                    exit_policy_hash=sig.exit_policy_hash,
                    signal_id=sig.signal_id,
                )
            signals.append(sig)

        signals.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))
        return signals




# --- from autotrade_v3/external/annotations.py
logger = logging.getLogger(__name__)

# --- from autotrade_v3/external/annotations.py
_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS annotations (
    source VARCHAR,
    symbol VARCHAR,
    as_of_ms BIGINT,
    payload_json VARCHAR,
    payload_hash VARCHAR,
    as_of_unverified BOOLEAN
)
"""








# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py




# --- from autotrade_v3/filters/c13_b1_vol_regime.py

# --- from autotrade_v3/filters/c13_b1_vol_regime.py

# --- from autotrade_v3/filters/c13_b1_vol_regime.py

# --- from autotrade_v3/filters/c13_b1_vol_regime.py



# --- from autotrade_v3/filters/c14_h3a_kumo_band.py

# --- from autotrade_v3/filters/c14_h3a_kumo_band.py

# --- from autotrade_v3/filters/c14_h3a_kumo_band.py

# --- from autotrade_v3/filters/c14_h3a_kumo_band.py



# --- from autotrade_v3/filters/c15_v5_sell_atr140.py

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py




# --- from autotrade_v3/filters/c3_b2_not_overextended_4h.py

# --- from autotrade_v3/filters/c3_b2_not_overextended_4h.py

# --- from autotrade_v3/filters/c3_b2_not_overextended_4h.py

# --- from autotrade_v3/filters/c3_b2_not_overextended_4h.py



# --- from autotrade_v3/filters/c6_r2_v4_combined.py

# --- from autotrade_v3/filters/c6_r2_v4_combined.py

# --- from autotrade_v3/filters/c6_r2_v4_combined.py

# --- from autotrade_v3/filters/c6_r2_v4_combined.py





# --- from autotrade_v3/filters/c7_r2_v2_asym.py

# --- from autotrade_v3/filters/c7_r2_v2_asym.py

# --- from autotrade_v3/filters/c7_r2_v2_asym.py



# --- from autotrade_v3/strategy/base.py
@dataclass(frozen=True)
class ExitPolicy__base:
    """Exit policy defining TP, SL, and Trailing parameters for a combo or strategy."""
    tp_pct: float
    sl_pct: float
    split_legs: bool = True
    trailing_act_pct: float = 0.005
    trailing_cb_pct: float = 0.005
    leg_weights: tuple[float, ...] = (0.5, 0.5)
    max_bars_held: int | None = None
    force_close_on_window_end: bool = False
    be_trigger_pct: float | None = None
    be_offset_pct: float | None = None

    def get_policy_hash(self) -> str:
        """Deterministic SHA256 of the ExitPolicy parameters."""
        data = asdict(self)
        if data.get('be_trigger_pct') is None:
            data.pop('be_trigger_pct', None)
        if data.get('be_offset_pct') is None:
            data.pop('be_offset_pct', None)
        raw = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['policy_hash'] = self.get_policy_hash()
        return d

# --- from autotrade_v3/strategy/base.py
class BaseStrategy(ABC):
    """Abstract Base Class for all pluggable trading strategies.

    Principles:
    - Strategy determines signals; Simulator determines execution; Analyzer determines metrics.
    - Strategy ONLY reads past and present candle data up to decision time (strictly no lookahead).
    - Strategy NEVER calls exchange APIs, never accesses live accounts, and never matches orders.
    """

    def __init__(self, config: dict[str, Any] | None=None):
        self.config = config or {}

    @property
    @abstractmethod
    def strategy_id(self) -> str:
        """Unique identifier of the strategy (e.g. 'LAB10_BASELINE' or '20FINAL')."""
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        """Version of the strategy (e.g. '1' or '1.0.0')."""
        pass

    @property
    @abstractmethod
    def required_timeframes(self) -> list[str]:
        """List of required timeframes (e.g. ['15m', '1h'])."""
        pass

    def get_signal_spec_hash(self) -> str:
        """Deterministic SHA256 of strategy_core source + signal-affecting parameters."""
        return canonical_json_sha256({'strategy_id': self.strategy_id, 'version': self.version, 'config': self.config})

    def get_exit_spec_hash(self) -> str:
        """Deterministic SHA256 of exit policies across all combos."""
        return canonical_json_sha256({'strategy_id': self.strategy_id, 'version': self.version, 'config': self.config})

    def get_spec_hash(self) -> str:
        """Combined SHA256 of signal_spec_hash, exit_spec_hash, strategy_id, and version."""
        return compute_spec_hash(self.get_signal_spec_hash(), self.get_exit_spec_hash(), self.strategy_id, self.version)

    def get_config_hash(self) -> str:
        """Deterministic SHA256 of the full strategy specification (delegates to get_spec_hash)."""
        return self.get_spec_hash()


    @abstractmethod
    def generate_signals(self, data: dict[str, pd.DataFrame], symbols: list[str] | None=None, only_approved: bool=True, only_warmup_ready: bool=True) -> list[Signal]:
        """Generate entry signals from data. Decisions strictly made at bar close."""
        pass

    @abstractmethod
    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy__base:
        """Retrieve exit policy for specific symbol and combo."""
        pass

    @abstractmethod
    def get_combo_config(self, symbol: str, combo_id: int) -> dict[str, Any]:
        """Retrieve static parameters for specific combo."""
        pass

    def to_dict(self) -> dict[str, Any]:
        return {'strategy_id': self.strategy_id, 'version': self.version, 'required_timeframes': self.required_timeframes, 'signal_spec_hash': self.get_signal_spec_hash(), 'exit_spec_hash': self.get_exit_spec_hash(), 'spec_hash': self.get_spec_hash(), 'config_hash': self.get_config_hash(), 'config': self.config}

# --- from autotrade_v3/strategy/baseline_20final.py
class Lab10BaseStrategy(BaseStrategy):
    """20FINAL Strategy adapter bridging Strategy Core to the V3 Research Lab."""

    def __init__(self, config: dict[str, Any] | None=None, core_strategy: Strategy | None=None, strategy_id_override: str | None=None, version_override: str | None=None):
        self._core = core_strategy or StrategyCoreRegistry.get('20FINAL', '1')
        spec = self._core.spec()
        merged_cfg = dict(config or {})
        if not merged_cfg:
            merged_cfg = {'combos': [c.to_dict() for c in spec.combos], 'warmup_bars': spec.warmup_bars, 'timeframes': list(spec.timeframes)}
        super().__init__(config=merged_cfg)
        self._engine = SignalEngine(core_strategy=self._core)
        self._strategy_id_override = strategy_id_override
        self._version_override = version_override

    @property
    def core_strategy(self) -> Strategy:
        return self._core

    def spec(self) -> StrategySpec:
        return self._core.spec()

    def generate(self, bars: dict[str, pd.DataFrame] | pd.DataFrame, symbols: list[str] | None=None, only_approved: bool=True, only_warmup_ready: bool=True) -> list[SignalEvent]:
        return self._core.generate(bars=bars, symbols=symbols, only_approved=only_approved, only_warmup_ready=only_warmup_ready)


    @property
    def strategy_id(self) -> str:
        if self._strategy_id_override is not None:
            return self._strategy_id_override
        sp = self._core.spec()
        if sp.strategy_id == '20FINAL' and sp.version == '1':
            return 'LAB10_BASELINE'
        return sp.strategy_id

    @property
    def version(self) -> str:
        if self._version_override is not None:
            return self._version_override
        sp = self._core.spec()
        if sp.strategy_id == '20FINAL' and sp.version == '1' and (self._strategy_id_override != '20FINAL'):
            return '1.0.0'
        return sp.version

    @property
    def required_timeframes(self) -> list[str]:
        return list(self._core.spec().timeframes)

    def get_signal_spec_hash(self) -> str:
        return self._core.spec().signal_spec_hash

    def get_exit_spec_hash(self) -> str:
        return self._core.spec().exit_spec_hash

    def get_spec_hash(self) -> str:
        return self._core.spec().spec_hash

    def get_config_hash(self) -> str:
        return self._core.spec().spec_hash


    def get_combo_config(self, symbol: str, combo_id: int) -> dict[str, Any]:
        return self._engine.get_combo_config(symbol, combo_id)

    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy__base:
        c_spec = self._engine.get_combo_spec(symbol, combo_id)
        ep = c_spec.exit_policy
        return ExitPolicy__base(tp_pct=float(ep.tp_pct), sl_pct=float(ep.sl_pct), split_legs=bool(ep.split_legs), trailing_act_pct=float(ep.trailing_act_pct), trailing_cb_pct=float(ep.trailing_cb_pct), leg_weights=tuple((float(w) for w in ep.leg_weights)), max_bars_held=ep.max_bars_held, force_close_on_window_end=bool(ep.force_close_on_window_end))

    def generate_signals(self, data: dict[str, pd.DataFrame], symbols: list[str] | None=None, only_approved: bool=True, only_warmup_ready: bool=True) -> list[Signal]:
        """Generate signals across provided symbols."""
        target_symbols = symbols or list(data.keys())
        all_signals: list[Signal] = []
        for sym in target_symbols:
            if sym not in data:
                continue
            df_m15 = data[sym]
            sigs = self._engine.generate_signals(symbol=sym, df_m15=df_m15, only_approved=only_approved, only_warmup_ready=only_warmup_ready)
            for s in sigs:
                ep = self.get_exit_policy(sym, s.combo_id)
                enriched = dataclasses.replace(s, exit_policy=ep, exit_policy_hash=ep.get_policy_hash())
                all_signals.append(enriched)
        all_signals.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))
        return all_signals

# --- from autotrade_v3/strategy/eth_c1_h1_both_1.py
logger = logging.getLogger(__name__)

# --- from autotrade_v3/strategy/eth_c1_h1_both_1.py
M15_MS = 15 * 60 * 1000

# --- from autotrade_v3/strategy/eth_c1_h1_both_1.py
ETH_C1_STRATEGY_ID = '20FINAL_FIXED_TPSL_ETH_C1_H1_BOTH_1'

# --- from autotrade_v3/strategy/eth_c1_h1_both_1.py
ETH_C1_STRATEGY_VERSION = '1.0.0'

# --- from autotrade_v3/strategy/eth_c1_h1_both_1.py
H1_BUY_MAX_SLOPE_4H_PCT = 1.5

# --- from autotrade_v3/strategy/eth_c1_h1_both_1.py
H1_SELL_MIN_DIST_EMA150_4H_PCT = -6.0

# --- from autotrade_v3/strategy/eth_c1_h1_both_1.py
REASON_H1_BUY_EMA_SLOPE = 'H1_BUY_EMA_SLOPE'

# --- from autotrade_v3/strategy/eth_c1_h1_both_1.py
REASON_H1_SELL_EMA_DISTANCE = 'H1_SELL_EMA_DISTANCE'

# --- from autotrade_v3/strategy/eth_c1_h1_both_1.py
REASON_SMC_VETO = 'SMC_VETO'

# --- from autotrade_v3/strategy/eth_c1_h1_both_1.py
_REQUIRED_OHLCV_COLS = ('open', 'high', 'low', 'close', 'volume')

# --- from autotrade_v3/strategy/eth_c1_h1_both_1.py
@dataclass(frozen=True)
class L2Decision:
    """Result of evaluating L2 (SMC_VETO + optional H1 filter) on a candidate signal."""
    approved: bool
    reason: str | None = None

# --- from autotrade_v3/strategy/eth_c1_h1_both_1.py
def compute_eth_c1_l2_features(df_m15: pd.DataFrame) -> dict[str, Any]:
    """Compute causal 1H and confirmed 4H EMA150 features for ETH-USDT/C1 from M15 OHLCV.

    Uses `align_confirmed(..., d1.index, "4h")` so at 1H bar open `ot` (closing at
    `ot + 1h`), only 4H bars already closed at or before `ot` are visible.
    """
    if not isinstance(df_m15, pd.DataFrame) or df_m15.empty:
        raise ValueError('ETH-USDT M15 candle DataFrame is missing or empty.')
    missing_cols = [c for c in _REQUIRED_OHLCV_COLS if c not in df_m15.columns]
    if missing_cols:
        raise ValueError(f'ETH-USDT M15 DataFrame missing required OHLCV columns: {missing_cols}')
    d15 = ensure_dt(df_m15)
    d1 = resample_ohlcv(d15, '1h')
    d4 = resample_ohlcv(d15, '4h')
    if d1.empty or d4.empty:
        raise ValueError('Resampled 1H or 4H DataFrame is empty for ETH-USDT.')
    ema150_4h_raw = ema(d4.close, 150)
    prev_ema150_4h = ema150_4h_raw.shift(10)
    slope_10bar_raw = (ema150_4h_raw - prev_ema150_4h) / prev_ema150_4h * 100.0
    ema150_4h_1h = align_confirmed(ema150_4h_raw, d1.index, '4h')
    slope_10bar_1h = align_confirmed(slope_10bar_raw, d1.index, '4h')
    dist_ema150_4h_pct = (d1.close - ema150_4h_1h) / ema150_4h_1h * 100.0
    a21 = atr(d1, 21)
    r14 = rsi(d1.close, 14)
    m14 = mfi(d1, 14)
    _, _, adx14 = dmi(d1, 14, 14)
    volma20 = sma(d1.volume, 20)
    k11 = stochastic(d1.close, d1.high, d1.low, 11)
    d3 = sma(k11, 3)
    c9 = cci(d1.close, d1.high, d1.low, 9)
    macd = ema(d1.close, 8) - ema(d1.close, 21)
    sig_m = ema(macd, 9)
    hist = macd - sig_m
    smc1 = smc_direction(d1, 50, False)
    ob1 = ob_context(d1, OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5))
    posmap = {ts: i for i, ts in enumerate(d15.index)}
    return {'d15': d15, 'd1': d1, 'd4': d4, 'a21': a21, 'ema150_4h_1h': ema150_4h_1h, 'slope_10bar_1h': slope_10bar_1h, 'dist_ema150_4h_pct': dist_ema150_4h_pct, 'r14': r14, 'm14': m14, 'adx14': adx14, 'volma20': volma20, 'k11': k11, 'd3': d3, 'c9': c9, 'hist': hist, 'smc1': smc1, 'ob1': ob1, 'posmap': posmap}

# --- from autotrade_v3/strategy/eth_c1_h1_both_1.py
def evaluate_eth_c1_l2(side: Literal['BUY', 'SELL'], smc_dir: int, ob_blocked: bool, layer2: str, *, slope_10bar_pct: float | None=None, dist_ema150_4h_pct: float | None=None, enable_h1: bool=True, buy_max_slope_4h_pct: float=H1_BUY_MAX_SLOPE_4H_PCT, sell_min_dist_ema150_4h_pct: float=H1_SELL_MIN_DIST_EMA150_4H_PCT) -> L2Decision:
    """Evaluate L2 (existing SMC_VETO/layer2 AND H1_BOTH_1) for ETH-USDT/C1.

    Raises ValueError if H1 is enabled and the required indicator value is missing or non-finite.
    """
    side_norm = str(side).upper()
    if side_norm not in ('BUY', 'SELL'):
        raise ValueError(f'Unsupported side for ETH-USDT/C1 L2 evaluation: {side!r}')
    side_code = 'L' if side_norm == 'BUY' else 'S'
    if not bool(approved(side_code, int(smc_dir), bool(ob_blocked), str(layer2))):
        return L2Decision(approved=False, reason=str(layer2) or REASON_SMC_VETO)
    if not enable_h1:
        return L2Decision(approved=True, reason=None)
    if side_norm == 'BUY':
        if slope_10bar_pct is None or not math.isfinite(float(slope_10bar_pct)):
            raise ValueError('Missing or non-finite ema150_4h_slope_10bar_pct for ETH-USDT/C1 BUY L2 check.')
        if float(slope_10bar_pct) > float(buy_max_slope_4h_pct):
            return L2Decision(approved=False, reason=REASON_H1_BUY_EMA_SLOPE)
        return L2Decision(approved=True, reason=None)
    if dist_ema150_4h_pct is None or not math.isfinite(float(dist_ema150_4h_pct)):
        raise ValueError('Missing or non-finite dist_ema150_4h_pct for ETH-USDT/C1 SELL L2 check.')
    if float(dist_ema150_4h_pct) < float(sell_min_dist_ema150_4h_pct):
        return L2Decision(approved=False, reason=REASON_H1_SELL_EMA_DISTANCE)
    return L2Decision(approved=True, reason=None)

# --- from autotrade_v3/strategy/eth_c1_h1_both_1.py
class EthC1H1Both1Strategy(Lab10BaseStrategy):
    """20FINAL with H1_BOTH_1 L2 filter integrated exclusively on ETH-USDT/C1.

    - ETH-USDT/C1 BUY:  `ema150_4h_slope_10bar_pct <= 1.5` (pre-edge, matching P3)
    - ETH-USDT/C1 SELL: `C < -80` AND `dist_ema150_4h_pct >= -6.0` (pre-edge, matching P3)
    - ETH-USDT/C1 Exit: TP 2.0%, SL 0.666667%, no trailing (`split_legs=False`)
    - Other 19 combos: identical signals to `20FINAL@1`.
    - Set `enable_eth_c1_h1_l2=False` for immediate rollback to L2 without H1.
    """

    def __init__(self, *, enable_eth_c1_h1_l2: bool=True, buy_max_slope_4h_pct: float=H1_BUY_MAX_SLOPE_4H_PCT, sell_min_dist_ema150_4h_pct: float=H1_SELL_MIN_DIST_EMA150_4H_PCT, pre_edge_h1: bool=True, **kwargs: Any):
        kwargs.setdefault('strategy_id_override', ETH_C1_STRATEGY_ID)
        kwargs.setdefault('version_override', ETH_C1_STRATEGY_VERSION)
        super().__init__(**kwargs)
        self.enable_eth_c1_h1_l2 = bool(enable_eth_c1_h1_l2)
        self.buy_max_slope_4h_pct = float(buy_max_slope_4h_pct)
        self.sell_min_dist_ema150_4h_pct = float(sell_min_dist_ema150_4h_pct)
        self.pre_edge_h1 = bool(pre_edge_h1)
        self.last_l2_rejections: list[dict[str, Any]] = []

    def _eth_c1_spec(self):
        matches = [c for c in self._core.spec().combos if c.symbol == 'ETH-USDT' and c.combo_id == 1]
        if len(matches) != 1:
            raise RuntimeError(f'Expected 1 ETH-USDT/C1 ComboSpec, found {len(matches)}')
        return matches[0]

    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy__base:
        ep = super().get_exit_policy(symbol, combo_id)
        if symbol == 'ETH-USDT' and int(combo_id) == 1:
            return ExitPolicy__base(tp_pct=ep.tp_pct, sl_pct=ep.sl_pct, split_legs=False, trailing_act_pct=0.0, trailing_cb_pct=0.0, leg_weights=(1.0,), max_bars_held=ep.max_bars_held, force_close_on_window_end=ep.force_close_on_window_end)
        return ep

    def get_signal_spec_hash(self) -> str:
        eth_c1 = self._eth_c1_spec()
        return canonical_json_sha256({'base_signal_spec_hash': super().get_signal_spec_hash(), 'strategy_id': self.strategy_id, 'version': self.version, 'eth_c1_sell_cci': 'C < -80', 'eth_c1_layer2': eth_c1.layer2, 'enable_eth_c1_h1_l2': self.enable_eth_c1_h1_l2, 'buy_max_slope_4h_pct': self.buy_max_slope_4h_pct, 'sell_min_dist_ema150_4h_pct': self.sell_min_dist_ema150_4h_pct, 'pre_edge_h1': self.pre_edge_h1})

    def get_exit_spec_hash(self) -> str:
        spec = self._core.spec()
        policies = {c.combo_key: self.get_exit_policy(c.symbol, c.combo_id).to_dict() for c in sorted(spec.combos, key=lambda x: x.combo_key)}
        return canonical_json_sha256({'strategy_id': self.strategy_id, 'version': self.version, 'exit_policies': policies})

    def get_spec_hash(self) -> str:
        return compute_spec_hash(self.get_signal_spec_hash(), self.get_exit_spec_hash(), self.strategy_id, self.version)

    def get_config_hash(self) -> str:
        return self.get_spec_hash()

    def _record_l2_rejection(self, *, signal_id: str, side: Literal['BUY', 'SELL'], signal_close_ms: int, reason: str, slope_val: float | None, dist_val: float | None) -> None:
        rec = {'signal_id': signal_id, 'symbol': 'ETH-USDT', 'combo_id': 1, 'combo_name': 'C1', 'side': side, 'signal_close_ms': int(signal_close_ms), 'reason': reason, 'ema150_4h_slope_10bar_pct': slope_val, 'dist_ema150_4h_pct': dist_val}
        self.last_l2_rejections.append(rec)
        logger.info('ETH-USDT/C1 L2 rejected %s signal_id=%s reason=%s slope_4h=%s dist_4h=%s', side, signal_id, reason, slope_val, dist_val)

    def _generate_eth_c1_side(self, features: dict[str, Any], *, side: Literal['BUY', 'SELL'], only_approved: bool=True, only_warmup_ready: bool=True) -> list[Signal]:
        """Generate ETH-USDT/C1 signals for one side with SMC_VETO + H1_BOTH_1 L2."""
        required_keys = ('d15', 'd1', 'a21', 'r14', 'm14', 'adx14', 'volma20', 'k11', 'd3', 'c9', 'hist', 'ema150_4h_1h', 'slope_10bar_1h', 'dist_ema150_4h_pct', 'smc1', 'ob1', 'posmap')
        missing_keys = [k for k in required_keys if k not in features]
        if missing_keys:
            raise KeyError(f'Missing ETH-USDT/C1 indicator features: {missing_keys}')
        d15 = features['d15']
        d1 = features['d1']
        a21 = features['a21']
        r14 = features['r14']
        m14 = features['m14']
        adx14 = features['adx14']
        volma20 = features['volma20']
        k11 = features['k11']
        d3 = features['d3']
        c9 = features['c9']
        hist = features['hist']
        ema150_4h_1h = features['ema150_4h_1h']
        slope_10bar_1h = features['slope_10bar_1h']
        dist_ema150_4h_pct = features['dist_ema150_4h_pct']
        smc1 = features['smc1']
        ob1 = features['ob1']
        posmap = features['posmap']
        eth_c1 = self._eth_c1_spec()
        volsp = d1.volume > volma20 * 1.5
        adxr = adx14 > adx14.shift(1)
        if side == 'BUY':
            mfi_long = float(eth_c1.entry.get('mfi_long', 50.0))
            sb08 = (d1.close > d1.open) & (d1.close - d1.open > a21 * 0.8)
            tu = d1.close > ema150_4h_1h
            longc = (k11 > 30) & (k11 > d3) & (c9 > 80) & (c9 > c9.shift(1))
            base_cond = longc & (r14 > 55) & (hist > 0) & (m14 > mfi_long) & adxr & sb08 & tu & volsp
            h1_series = slope_10bar_1h
            h1_ok_series = h1_series.notna() & (h1_series <= self.buy_max_slope_4h_pct)
            h1_reason = REASON_H1_BUY_EMA_SLOPE
        else:
            mfi_short = float(eth_c1.entry.get('mfi_short', 50.0))
            ss08 = (d1.close < d1.open) & (d1.open - d1.close > a21 * 0.8)
            td = d1.close < ema150_4h_1h
            shortc = (k11 < 70) & (k11 < d3) & (c9 < -80) & (c9 < c9.shift(1))
            base_cond = shortc & (r14 < 45) & (hist < 0) & (m14 < mfi_short) & adxr & ss08 & td & volsp
            h1_series = dist_ema150_4h_pct
            h1_ok_series = h1_series.notna() & (h1_series >= self.sell_min_dist_ema150_4h_pct)
            h1_reason = REASON_H1_SELL_EMA_DISTANCE
        ev_base = edge_event(base_cond)
        warmup_bars = self._core.spec().warmup_bars
        if self.enable_eth_c1_h1_l2:
            for ot in ev_base.index[ev_base.fillna(False)]:
                last = ot + pd.Timedelta(minutes=45)
                if last not in posmap:
                    continue
                pos = posmap[last]
                if pos >= warmup_bars - 1:
                    val = float(h1_series.loc[ot])
                    ema_val = float(ema150_4h_1h.loc[ot])
                    if not math.isfinite(ema_val) or not math.isfinite(val):
                        raise ValueError(f'Missing or non-finite 4H EMA150 indicator for ETH-USDT/C1 {side} at {ot}: ema150_4h={ema_val!r}, h1_metric={val!r}')
            ev = edge_event(base_cond & h1_ok_series) if self.pre_edge_h1 else ev_base & h1_ok_series
        else:
            ev = ev_base
        if self.enable_eth_c1_h1_l2:
            for ot in ev_base.index[ev_base.fillna(False)]:
                last = ot + pd.Timedelta(minutes=45)
                if last not in posmap:
                    continue
                pos = posmap[last]
                if pos < warmup_bars - 1:
                    continue
                if not bool(ev.loc[ot]):
                    close_ms = int(d15.index[pos].value // 1000000 + M15_MS - 1)
                    sig_id = f'SIG_ETHUSDT_{eth_c1.combo_name}_{eth_c1.native_timeframe}_{side}_{close_ms}'
                    s_val = float(slope_10bar_1h.loc[ot]) if pd.notna(slope_10bar_1h.loc[ot]) else None
                    d_val = float(dist_ema150_4h_pct.loc[ot]) if pd.notna(dist_ema150_4h_pct.loc[ot]) else None
                    self._record_l2_rejection(signal_id=sig_id, side=side, signal_close_ms=close_ms, reason=h1_reason, slope_val=s_val, dist_val=d_val)
        ep = self.get_exit_policy(eth_c1.symbol, eth_c1.combo_id)
        ep_hash = ep.get_policy_hash()
        ob_col = 'buy_blocked' if side == 'BUY' else 'sell_blocked'
        out: list[Signal] = []
        for ot in ev.index[ev.fillna(False)]:
            last = ot + pd.Timedelta(minutes=45)
            if last not in posmap:
                continue
            pos = posmap[last]
            warmup_ok = pos >= warmup_bars - 1
            sd = int(smc1.loc[ot])
            blocked = bool(ob1.loc[ot][ob_col])
            s_val = float(slope_10bar_1h.loc[ot]) if pd.notna(slope_10bar_1h.loc[ot]) else None
            d_val = float(dist_ema150_4h_pct.loc[ot]) if pd.notna(dist_ema150_4h_pct.loc[ot]) else None
            if warmup_ok:
                dec = evaluate_eth_c1_l2(side, sd, blocked, eth_c1.layer2, slope_10bar_pct=s_val, dist_ema150_4h_pct=d_val, enable_h1=self.enable_eth_c1_h1_l2, buy_max_slope_4h_pct=self.buy_max_slope_4h_pct, sell_min_dist_ema150_4h_pct=self.sell_min_dist_ema150_4h_pct)
            else:
                side_code = 'L' if side == 'BUY' else 'S'
                smc_ok = bool(approved(side_code, sd, blocked, eth_c1.layer2))
                dec = L2Decision(approved=smc_ok, reason=None if smc_ok else eth_c1.layer2)
            close_ms = int(d15.index[pos].value // 1000000 + M15_MS - 1)
            sig_id = f'SIG_ETHUSDT_{eth_c1.combo_name}_{eth_c1.native_timeframe}_{side}_{close_ms}'
            if not dec.approved and warmup_ok and (dec.reason is not None):
                self._record_l2_rejection(signal_id=sig_id, side=side, signal_close_ms=close_ms, reason=dec.reason, slope_val=s_val, dist_val=d_val)
            if only_approved and (not dec.approved):
                continue
            if only_warmup_ready and (not warmup_ok):
                continue
            bar_open_ms = int(d15.index[pos].value // 1000000)
            ref_price = float(d15.iloc[pos]['close'])
            provider = str(d15.iloc[pos].get('provider', 'UNKNOWN'))
            tp_px = ref_price * (1.0 + ep.tp_pct) if side == 'BUY' else ref_price * (1.0 - ep.tp_pct)
            sl_px = ref_price * (1.0 - ep.sl_pct) if side == 'BUY' else ref_price * (1.0 + ep.sl_pct)
            out.append(Signal(symbol=eth_c1.symbol, combo_id=eth_c1.combo_id, combo_name=eth_c1.combo_name, timeframe=eth_c1.native_timeframe, bar_position=pos, bar_open_ms=bar_open_ms, signal_close_ms=close_ms, signal_close_dt=d15.index[pos] + pd.Timedelta(milliseconds=M15_MS - 1), side=side, entry_price=ref_price, tp_price=tp_px, sl_price=sl_px, tp_pct=ep.tp_pct, sl_pct=ep.sl_pct, approved=dec.approved, warmup_ready=warmup_ok, smc_dir=sd, ob_blocked=blocked, is_new6=eth_c1.is_new6, provider=provider, signal_id=sig_id, exit_policy=ep, exit_policy_hash=ep_hash))
        return out

    def generate_signals(self, data: dict[str, pd.DataFrame], symbols: list[str] | None=None, only_approved: bool=True, only_warmup_ready: bool=True) -> list[Signal]:
        """Generate signals across symbols, applying H1_BOTH_1 L2 exclusively to ETH-USDT/C1."""
        self.last_l2_rejections = []
        base_sigs = super().generate_signals(data=data, symbols=symbols, only_approved=only_approved, only_warmup_ready=only_warmup_ready)
        target_symbols = symbols or list(data.keys())
        if 'ETH-USDT' not in target_symbols or 'ETH-USDT' not in data:
            return base_sigs
        eth_features = compute_eth_c1_l2_features(data['ETH-USDT'])
        kept_non_c1 = [s for s in base_sigs if not (s.symbol == 'ETH-USDT' and s.combo_id == 1)]
        buys = self._generate_eth_c1_side(eth_features, side='BUY', only_approved=only_approved, only_warmup_ready=only_warmup_ready)
        sells = self._generate_eth_c1_side(eth_features, side='SELL', only_approved=only_approved, only_warmup_ready=only_warmup_ready)
        combined = kept_non_c1 + buys + sells
        combined.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))
        return combined

# --- from autotrade_v3/strategy/v3_lab_8.py

# --- from autotrade_v3/strategy/v3_lab_8.py

# --- from autotrade_v3/strategy/v3_lab_8.py
M15_MS = 900000

# --- from autotrade_v3/strategy/v3_lab_8.py
def _index_utc_open_ms(idx: pd.DatetimeIndex) -> np.ndarray:
    idx_utc = idx.tz_localize('UTC') if idx.tz is None else idx.tz_convert('UTC')
    return np.asarray(idx_utc.as_unit('ms').asi8, dtype=np.int64)

# --- from autotrade_v3/strategy/v3_lab_8.py
def _build_v3_lab_8_core(params_dir: Path | None=None, version: str='8.0.0') -> Strategy:
    """Build self-contained 30-combo Fixed TP/SL core for V3_LAB_8."""
    base_core = StrategyCoreRegistry.get('20FINAL_FIXED_V1', '1', params_dir=params_dir)
    base_spec = base_core.spec()
    combos = [copy.deepcopy(c) for c in base_spec.combos]
    existing = {(c.combo_id, c.symbol) for c in combos}
    by_id: dict[int, list[ComboSpec]] = {}
    for c in combos:
        by_id.setdefault(c.combo_id, []).append(c)
    for cid in sorted(by_id):
        if len(by_id[cid]) == 1:
            src = by_id[cid][0]
            tgt = 'ETH-USDT' if src.symbol == 'BTC-USDT' else 'BTC-USDT'
            if (cid, tgt) not in existing:
                combos.append(dataclasses.replace(copy.deepcopy(src), symbol=tgt, combo_key=f'{tgt}/{src.combo_name}'))
    if len(combos) != 30:
        raise RuntimeError(f'Expected 30 combos in V3_LAB_8 core, got {len(combos)}')
    updated: list[ComboSpec] = []
    for c in combos:
        if c.symbol == 'ETH-USDT' and int(c.combo_id) == 10:
            c = dataclasses.replace(c, layer2='SMC_VETO')
        elif c.symbol == 'ETH-USDT' and int(c.combo_id) == 101:
            new_entry = dict(c.entry)
            new_entry['indicator'] = 'DeMarker'
            new_entry['period'] = 60
            new_entry['long_min'] = 0.78
            new_entry['short_max'] = 0.2
            c = dataclasses.replace(c, entry=new_entry)
        elif c.symbol == 'BTC-USDT' and int(c.combo_id) == 101:
            new_entry = dict(c.entry)
            new_entry['indicator'] = 'DeMarker'
            new_entry['period'] = 60
            new_entry['long_min'] = 0.78
            new_entry['short_max'] = 0.25
            c = dataclasses.replace(c, entry=new_entry)
        elif c.symbol == 'BTC-USDT' and int(c.combo_id) == 106:
            new_entry = dict(c.entry)
            new_entry['indicator'] = 'Vortex'
            new_entry['period'] = 14
            new_entry['ratio'] = 1.22
            c = dataclasses.replace(c, entry=new_entry)
        updated.append(c)

    class _V3Lab8Core(Strategy):

        def __init__(self, inner: Strategy, new_combos: list[ComboSpec]) -> None:
            inner_copy: Any = copy.deepcopy(inner)
            self._inner: Any = inner_copy
            self._spec = dataclasses.replace(inner_copy.spec(), strategy_id='V3_LAB_8', version=version, combos=tuple(new_combos))
            setattr(inner_copy, '_spec', self._spec)

        def spec(self) -> StrategySpec:
            return self._spec

        def generate(self, bars: dict[str, pd.DataFrame] | pd.DataFrame, symbols: list[str] | None=None, only_approved: bool=True, only_warmup_ready: bool=True) -> list[SignalEvent]:
            return list(self._inner.generate(bars=bars, symbols=symbols, only_approved=only_approved, only_warmup_ready=only_warmup_ready))

        def on_bar_close(self, state: Any, bar: Any, only_approved: bool=True, only_warmup_ready: bool=True) -> list[SignalEvent]:
            return list(self._inner.on_bar_close(state=state, bar=bar, only_approved=only_approved, only_warmup_ready=only_warmup_ready))
    return _V3Lab8Core(base_core, updated)

# --- from autotrade_v3/strategy/v3_lab_8.py
class V3Lab8Strategy(Lab10BaseStrategy):
    """Self-contained official V3_LAB_8@8.0.0 strategy (single-file wiring for all 30 combos)."""
    strategy_id: str = 'V3_LAB_8'
    version: str = '8.0.0'
    SUPPORTED_VERSIONS = ('8.0.0',)

    def __init__(self, params_dir: Path | None=None, version: str='8.0.0') -> None:
        if version not in self.SUPPORTED_VERSIONS:
            raise ValueError(f'Unsupported V3_LAB_8 version {version!r}; expected {self.SUPPORTED_VERSIONS}')
        self._version_value = version
        core30 = _build_v3_lab_8_core(params_dir=params_dir, version=version)
        super().__init__(core_strategy=core30, strategy_id_override=self.strategy_id, version_override=version)
        self._h1_both_1 = EthC1H1Both1Strategy(enable_eth_c1_h1_l2=True, buy_max_slope_4h_pct=H1_BUY_MAX_SLOPE_4H_PCT, sell_min_dist_ema150_4h_pct=H1_SELL_MIN_DIST_EMA150_4H_PCT, pre_edge_h1=True)

    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy__base:
        if symbol == 'ETH-USDT' and int(combo_id) == 1:
            ep = self._h1_both_1.get_exit_policy(symbol, combo_id)
        else:
            ep = super().get_exit_policy(symbol, combo_id)
        if ep.split_legs or float(ep.trailing_act_pct) != 0.0:
            raise ValueError(f'V3_LAB_8 requires Fixed TP/SL without trailing/split for {symbol}/{combo_id}: {ep}')
        return ep

    def get_signal_spec_hash(self) -> str:
        spec_dict: dict[str, Any] = {'strategy_id': self.strategy_id, 'version': self._version_value, 'eth_c1': f'H1_BOTH_1({H1_BUY_MAX_SLOPE_4H_PCT},{H1_SELL_MIN_DIST_EMA150_4H_PCT})', 'btc_c1': 'H2_BUY_VOLUME_1_8', 'eth_c6': 'R2_V4_COMBINED(buy>=1.2,sell>=0.9)', 'eth_c7': 'R2_V2_ASYM_BUY_VOL20_SELL_TREND', 'btc_c11': 'H1_BUY_0_78', 'eth_c11': 'H3_B1_DEMARKER', 'eth_c12': 'V3_ATR065_NO_EXT24H', 'btc_c14': 'H3a_KUMO_BAND_V1', 'eth_c14': 'A-R2-05_ATR_SL_ADJUSTMENT', 'eth_c15': 'V5_SELL_ONLY_ATR140', 'btc_c16': 'H3_VORTEX_RATIO_1_22'}
        return canonical_json_sha256(spec_dict)

    def get_exit_spec_hash(self) -> str:
        spec = self._core.spec()
        policies = {c.combo_key: self.get_exit_policy(c.symbol, c.combo_id).to_dict() for c in sorted(spec.combos, key=lambda x: x.combo_key)}
        return canonical_json_sha256({'strategy_id': self.strategy_id, 'version': self.version, 'exit_policies': policies})

    def get_spec_hash(self) -> str:
        return compute_spec_hash(self.get_signal_spec_hash(), self.get_exit_spec_hash(), self.strategy_id, self.version)

    @staticmethod
    def _filter_eth_c10_asymmetric(signals: list[Signal], eth_m15: pd.DataFrame | None) -> list[Signal]:
        if eth_m15 is None or eth_m15.empty:
            return signals
        d15 = ensure_dt(eth_m15)
        smc15 = smc_direction(d15, 50, False)
        open_ms_arr = _index_utc_open_ms(pd.DatetimeIndex(smc15.index))
        smc_by_open_ms = {int(oms): int(val) for oms, val in zip(open_ms_arr, smc15.to_numpy(dtype=np.int64))}
        out: list[Signal] = []
        for s in signals:
            if s.symbol == 'ETH-USDT' and int(s.combo_id) == 10 and (s.side == 'SELL'):
                if smc_by_open_ms.get(int(s.signal_close_ms) + 1 - M15_MS, 0) != -1:
                    continue
            out.append(s)
        return out

    @staticmethod
    def _filter_4h_sell_downtrend(signals: list[Signal], sym_m15: pd.DataFrame | None, symbol: str, combo_id: int) -> list[Signal]:
        if sym_m15 is None or sym_m15.empty:
            return [s for s in signals if not (s.symbol == symbol and int(s.combo_id) == combo_id and (s.side == 'SELL'))]
        d4 = resample_ohlcv(ensure_dt(sym_m15), '4h')
        if d4.empty:
            return [s for s in signals if not (s.symbol == symbol and int(s.combo_id) == combo_id and (s.side == 'SELL'))]
        ema100_4h = ema(d4['close'], 100)
        d4_close_ms = datetime_index_to_ms(d4.index) + H4_MS - 1
        d4_closes = d4['close'].to_numpy(dtype=float)
        d4_emas = ema100_4h.to_numpy(dtype=float)
        out: list[Signal] = []
        for s in signals:
            if s.symbol == symbol and int(s.combo_id) == combo_id and (s.side == 'SELL'):
                idx4 = int(np.searchsorted(d4_close_ms, int(s.signal_close_ms), side='right')) - 1
                if idx4 < 0 or not (np.isfinite(d4_closes[idx4]) and np.isfinite(d4_emas[idx4]) and (d4_closes[idx4] < d4_emas[idx4])):
                    continue
            out.append(s)
        return out

    @staticmethod
    def _filter_batch_a_one_sided(signals: list[Signal]) -> list[Signal]:
        drop_rules = {('BTC-USDT', 2, 'BUY'), ('BTC-USDT', 4, 'SELL'), ('BTC-USDT', 7, 'SELL'), ('BTC-USDT', 103, 'BUY'), ('ETH-USDT', 103, 'BUY')}
        return [s for s in signals if (s.symbol, int(s.combo_id), s.side) not in drop_rules]

    @staticmethod
    def _filter_btc_c16_vortex_ratio(signals: list[Signal], btc_m15: pd.DataFrame | None, min_ratio: float=1.22) -> list[Signal]:
        if btc_m15 is None or btc_m15.empty:
            return [s for s in signals if not (s.symbol == 'BTC-USDT' and int(s.combo_id) == 106)]
        d1 = resample_ohlcv(ensure_dt(btc_m15), '1h')
        if d1.empty:
            return [s for s in signals if not (s.symbol == 'BTC-USDT' and int(s.combo_id) == 106)]
        vi_p, vi_m = _vortex(d1['high'].to_numpy(dtype=float), d1['low'].to_numpy(dtype=float), d1['close'].to_numpy(dtype=float), 14)
        d1_close_ms = datetime_index_to_ms(d1.index) + H1_MS - 1
        vortex_by_ms = {int(d1_close_ms[i]): (float(vi_p[i]), float(vi_m[i])) for i in range(len(d1_close_ms))}
        out: list[Signal] = []
        for s in signals:
            if s.symbol == 'BTC-USDT' and int(s.combo_id) == 106:
                vp, vm = vortex_by_ms.get(int(s.signal_close_ms), (np.nan, np.nan))
                if not (np.isfinite(vp) and np.isfinite(vm) and (vp > 0.0) and (vm > 0.0)):
                    continue
                if s.side == 'BUY' and (not vp / vm > min_ratio) or (s.side == 'SELL' and (not vm / vp > min_ratio)):
                    continue
            out.append(s)
        return out

    @staticmethod
    def _apply_eth_c14_atr_sl(signals: list[Signal], eth_m15: pd.DataFrame | None) -> list[Signal]:
        kumo_by_ms: dict[int, float] = {}
        atr_pct_by_ms: dict[int, float] = {}
        if eth_m15 is not None and (not eth_m15.empty):
            d1 = resample_ohlcv(ensure_dt(eth_m15), '1h')
            if not d1.empty:
                h1, l1, c1 = (d1['high'].to_numpy(dtype=float), d1['low'].to_numpy(dtype=float), d1['close'].to_numpy(dtype=float))
                _, _, span_a, span_b = _ichimoku(h1, l1, 12, 30, 60)
                a14 = atr(d1, 14).to_numpy(dtype=float)
                d1_close_ms = datetime_index_to_ms(d1.index) + H1_MS - 1
                for i in range(len(d1)):
                    cms = int(d1_close_ms[i])
                    if np.isfinite(span_a[i]) and np.isfinite(span_b[i]) and (c1[i] > 0):
                        kumo_by_ms[cms] = float(abs(span_a[i] - span_b[i]) / c1[i])
                    if np.isfinite(a14[i]) and c1[i] > 0:
                        atr_pct_by_ms[cms] = float(a14[i] / c1[i])
        out: list[Signal] = []
        for s in signals:
            if s.symbol == 'ETH-USDT' and int(s.combo_id) == 104:
                cms = int(s.signal_close_ms)
                kp = kumo_by_ms.get(cms, np.nan)
                if not (np.isfinite(kp) and 0.005 <= kp < 0.01):
                    continue
                sl_pct = max(0.006, min(0.012, 1.0 * float(atr_pct_by_ms.get(cms, 0.009))))
                ep = ExitPolicy__base(tp_pct=0.02, sl_pct=sl_pct, split_legs=False, trailing_act_pct=0.0, trailing_cb_pct=0.0, leg_weights=(1.0,))
                out.append(dataclasses.replace(s, sl_pct=sl_pct, exit_policy=ep, exit_policy_hash=ep.get_policy_hash()))
            else:
                out.append(s)
        return out

    def _regenerate_btc_c1_buy_vol_1_8(self, btc_m15: pd.DataFrame, *, only_approved: bool, only_warmup_ready: bool) -> list[Signal]:
        d15 = ensure_dt(btc_m15).tz_convert('UTC')
        pc = precompute(d15, None)
        d1, p1 = (pc['d1'], pc['1'])
        c_spec = next((c for c in self.spec().combos if c.symbol == 'BTC-USDT' and int(c.combo_id) == 1))
        ep = self.get_exit_policy('BTC-USDT', 1)
        ep_hash = ep.get_policy_hash()
        warmup_bars = int(self.spec().warmup_bars)
        posmap = {ts: i for i, ts in enumerate(d15.index)}
        A21, R, M, ADX, K, D, C = (p1['A21'], p1['R'], p1['M'], p1['ADX'], p1['K'], p1['D'], p1['C'])
        macd = ema(d1.close, 8) - ema(d1.close, 21)
        hist = macd - ema(macd, 9)
        volsp = d1.volume > p1['volma'] * 1.8
        adxr = ADX > ADX.shift(1)
        sb08 = (d1.close > d1.open) & (d1.close - d1.open > A21 * 0.8)
        tu = d1.close > p1['ema1504']
        longc = (K > 30) & (K > D) & (C > 80) & (C > C.shift(1))
        lth = float(c_spec.entry.get('mfi_long', 50.0))
        L = edge_event(longc & (R > 55) & (hist > 0) & (M > lth) & adxr & sb08 & tu & volsp)
        smc_s = smc_direction(d1, 50, False)
        ob_df = ob_context(d1, OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5))
        offset = pd.Timedelta(minutes=45)
        out: list[Signal] = []
        for ot in L.index[L.fillna(False)]:
            last = ot + offset
            if last not in posmap:
                continue
            sd = int(smc_s.loc[ot])
            blocked = bool(ob_df.loc[ot, 'buy_blocked'])
            ok = bool(approved('L', sd, blocked, c_spec.layer2))
            ev = _make_signal_event('BTC-USDT', c_spec, 'L', ot, posmap[last], sd, blocked, ok, d15, warmup_bars)
            if only_approved and (not ev.tags['approved']) or (only_warmup_ready and (not ev.tags['warmup_ready'])):
                continue
            out.append(dataclasses.replace(signal_from_event(ev, c_spec), exit_policy=ep, exit_policy_hash=ep_hash))
        return out

    def generate_signals(self, data: dict[str, pd.DataFrame], symbols: Collection[str] | None=None, only_approved: bool=True, only_warmup_ready: bool=True) -> list[Signal]:
        """Generate all 30-combo V3_LAB_8 signals strictly from closed OHLCV bars."""
        sym_list = list(symbols) if symbols is not None else None
        base_sigs = super().generate_signals(data=data, symbols=sym_list, only_approved=only_approved, only_warmup_ready=only_warmup_ready)
        btc_m15 = data.get('BTC-USDT')
        eth_m15 = data.get('ETH-USDT')
        ctx = FilterContext()
        if btc_m15 is not None and (not btc_m15.empty):
            h3a_flt = C14H3aKumoBandFilter.from_1h_dataframe(resample_ohlcv(ensure_dt(btc_m15), '1h'))
            kept = [s for s in base_sigs if not (s.symbol == 'ETH-USDT' and int(s.combo_id) == 1) and h3a_flt.evaluate(s, ctx).action == 'KEEP']
        else:
            kept = [s for s in base_sigs if not (s.symbol == 'ETH-USDT' and int(s.combo_id) == 1)]
        kept = self._filter_eth_c10_asymmetric(kept, eth_m15)
        kept = self._filter_4h_sell_downtrend(kept, eth_m15, 'ETH-USDT', 102)
        kept = self._filter_batch_a_one_sided(kept)
        kept = self._filter_4h_sell_downtrend(kept, btc_m15, 'BTC-USDT', 103)
        kept = self._filter_4h_sell_downtrend(kept, eth_m15, 'ETH-USDT', 103)
        kept = self._filter_btc_c16_vortex_ratio(kept, btc_m15, min_ratio=1.22)
        kept = self._apply_eth_c14_atr_sl(kept, eth_m15)
        target_syms = set(symbols) if symbols is not None else set(data.keys())
        if 'BTC-USDT' in target_syms and btc_m15 is not None and (not btc_m15.empty):
            kept = [s for s in kept if not (s.symbol == 'BTC-USDT' and int(s.combo_id) == 1 and (s.side == 'BUY'))]
            kept.extend(self._regenerate_btc_c1_buy_vol_1_8(btc_m15, only_approved=only_approved, only_warmup_ready=only_warmup_ready))
        if 'ETH-USDT' in target_syms and eth_m15 is not None and (not eth_m15.empty):
            h1_sigs = self._h1_both_1.generate_signals(data=data, symbols=['ETH-USDT'], only_approved=only_approved, only_warmup_ready=only_warmup_ready)
            kept.extend((s for s in h1_sigs if s.symbol == 'ETH-USDT' and int(s.combo_id) == 1))
            eth_dt = ensure_dt(eth_m15)
            eth_15m = resample_ohlcv(eth_dt, '15min')
            eth_1h = resample_ohlcv(eth_dt, '1h')
            c15_flt = C15V5SellAtr140Filter.from_m15_dataframe(eth_m15)
            c12_ms, c12_atr, c12_ret = build_c12_1h_features_lookup(eth_1h)
            c12_flt = C12V3Atr065NoExt24hFilter(c12_ms, c12_atr, c12_ret)
            c15_ms, c7_vol, c7_1h_ms, c7_ema = build_c7_features_lookup(eth_15m, eth_1h)
            c7_flt = C7R2V2AsymFilter(c15_ms, c7_vol, c7_1h_ms, c7_ema)
            c6_ms, c6_atr_ratio = build_c6_atr_ratio_lookup(eth_1h)
            c6_flt = C6R2V4CombinedFilter(c6_ms, c6_atr_ratio)
            eth_filtered: list[Signal] = []
            for s in kept:
                if s.symbol == 'ETH-USDT':
                    cid = int(s.combo_id)
                    dec = None
                    if cid == 105:
                        dec = c15_flt.evaluate(s, ctx)
                    elif cid == C12_COMBO_ID:
                        ok, reason = c12_flt.evaluate_signal(s)
                        if not ok:
                            if only_approved:
                                continue
                            s = Signal(**{**s.__dict__, 'approved': False, 'rejection_reason': reason})
                    elif cid == C7_COMBO_ID:
                        dec = c7_flt.evaluate_signal(s)
                    elif cid == C6_COMBO_ID:
                        dec = c6_flt.evaluate_signal(s)
                    if dec is not None and dec.action != 'KEEP':
                        if only_approved:
                            continue
                        s = Signal(**{**s.__dict__, 'approved': False, 'rejection_reason': dec.reason})
                eth_filtered.append(s)
            kept = eth_filtered
            cfg_c6 = self.get_combo_config(C6_SYMBOL, C6_COMBO_ID)
            ep_c6 = self.get_exit_policy(C6_SYMBOL, C6_COMBO_ID)
            for s_sig in generate_c6_cci_audit_sell_signals(eth_dt, combo_name=str(cfg_c6.get('name', 'C6')), tp_pct=float(cfg_c6['tp_pct']), sl_pct=float(cfg_c6['sl_pct']), strategy_id=self.strategy_id, exit_policy=ep_c6, exit_policy_hash=ep_c6.get_policy_hash(), warmup_end_ms=None):
                dec = c6_flt.evaluate_signal(s_sig)
                if dec.action != 'KEEP':
                    if only_approved:
                        continue
                    s_sig = Signal(**{**s_sig.__dict__, 'approved': False, 'rejection_reason': dec.reason})
                kept.append(s_sig)
        kept.sort(key=lambda s: (int(s.signal_close_ms), s.symbol, int(s.combo_id), s.side))
        return kept

    def generate_signals_for_combos(self, data: dict[str, pd.DataFrame], *, allowed_combos: Collection[tuple[str, int]] | None=None, symbols: list[str] | None=None, only_approved: bool=True, only_warmup_ready: bool=True) -> list[Signal]:
        """Generate V3_LAB_8 signals restricted to an optional ``allowed_combos`` subset."""
        if allowed_combos is None:
            return self.generate_signals(data=data, symbols=symbols, only_approved=only_approved, only_warmup_ready=only_warmup_ready)
        allowed = {(str(sym), int(cid)) for sym, cid in allowed_combos}
        if not allowed:
            return []
        allowed_syms = {sym for sym, _ in allowed}
        eff_symbols = [sym for sym in (symbols if symbols is not None else data.keys()) if sym in allowed_syms]
        if not eff_symbols:
            return []
        sigs = self.generate_signals(data=data, symbols=eff_symbols, only_approved=only_approved, only_warmup_ready=only_warmup_ready)
        return [s for s in sigs if (s.symbol, int(s.combo_id)) in allowed]

# --- from autotrade_v3/strategy/v3_lab_9.py
class V3Lab9Strategy(V3Lab8Strategy):
    """Official V3_LAB_9@9.0.0 strategy (V3_LAB_8 + ETH-USDT/C3 B2_NOT_OVEREXTENDED_4H)."""

    strategy_id: str = "V3_LAB_9"
    version: str = "9.0.0"
    SUPPORTED_VERSIONS = ("9.0.0",)

    def __init__(self, params_dir: Path | None = None, version: str = "9.0.0") -> None:
        super().__init__(params_dir=params_dir, version=version)

    def get_signal_spec_hash(self) -> str:
        spec_dict: dict[str, Any] = {
            "strategy_id": self.strategy_id,
            "version": self._version_value,
            "eth_c1": f"H1_BOTH_1({H1_BUY_MAX_SLOPE_4H_PCT},{H1_SELL_MIN_DIST_EMA150_4H_PCT})",
            "btc_c1": "H2_BUY_VOLUME_1_8",
            "eth_c3": f"B2_NOT_OVEREXTENDED_4H(ema_trend_4h_dir<={C3_MAX_EMA_TREND_4H_DIR})",
            "eth_c6": "R2_V4_COMBINED(buy>=1.2,sell>=0.9)",
            "eth_c7": "R2_V2_ASYM_BUY_VOL20_SELL_TREND",
            "btc_c11": "H1_BUY_0_78",
            "eth_c11": "H3_B1_DEMARKER",
            "eth_c12": "V3_ATR065_NO_EXT24H",
            "btc_c14": "H3a_KUMO_BAND_V1",
            "eth_c14": "A-R2-05_ATR_SL_ADJUSTMENT",
            "eth_c15": "V5_SELL_ONLY_ATR140",
            "btc_c16": "H3_VORTEX_RATIO_1_22",
        }
        return canonical_json_sha256(spec_dict)

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: Collection[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate all 30-combo V3_LAB_9 signals strictly from closed OHLCV bars."""
        base_signals = super().generate_signals(
            data=data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )

        eth_df = data.get(C3_SYMBOL)
        if eth_df is None or eth_df.empty:
            return base_signals

        c3_filter = C3B2NotOverextended4hFilter.from_m15_dataframe(eth_df)

        out: list[Signal] = []
        for sig in base_signals:
            if sig.symbol == C3_SYMBOL and int(sig.combo_id) == C3_COMBO_ID:
                dec = c3_filter.evaluate_signal(sig)
                if dec.action != "KEEP":
                    if only_approved:
                        continue
                    sig = Signal(
                        **{
                            **sig.__dict__,
                            "approved": False,
                            "rejection_reason": dec.reason,
                        }
                    )
            out.append(sig)
        return out

# --- from autotrade_v3/strategy/v3_lab_10.py
class V3Lab10Strategy(V3Lab9Strategy):
    """Official V3_LAB_10@10.0.0 strategy (V3_LAB_9 + ETH-USDT/C13 B1_VOL_REGIME)."""

    strategy_id: str = "V3_LAB_10"
    version: str = "10.0.0"
    SUPPORTED_VERSIONS = ("10.0.0",)

    def __init__(self, params_dir: Path | None = None, version: str = "10.0.0") -> None:
        super().__init__(params_dir=params_dir, version=version)

    def get_signal_spec_hash(self) -> str:
        spec_dict: dict[str, Any] = {
            "strategy_id": self.strategy_id,
            "version": self._version_value,
            "eth_c1": f"H1_BOTH_1({H1_BUY_MAX_SLOPE_4H_PCT},{H1_SELL_MIN_DIST_EMA150_4H_PCT})",
            "btc_c1": "H2_BUY_VOLUME_1_8",
            "eth_c3": f"B2_NOT_OVEREXTENDED_4H(ema_trend_4h_dir<={C3_MAX_EMA_TREND_4H_DIR})",
            "eth_c6": "R2_V4_COMBINED(buy>=1.2,sell>=0.9)",
            "eth_c7": "R2_V2_ASYM_BUY_VOL20_SELL_TREND",
            "btc_c11": "H1_BUY_0_78",
            "eth_c11": "H3_B1_DEMARKER",
            "eth_c12": "V3_ATR065_NO_EXT24H",
            "eth_c13": f"B1_VOL_REGIME(atr_ratio_50<={C13_MAX_ATR_RATIO_50})",
            "btc_c14": "H3a_KUMO_BAND_V1",
            "eth_c14": "A-R2-05_ATR_SL_ADJUSTMENT",
            "eth_c15": "V5_SELL_ONLY_ATR140",
            "btc_c16": "H3_VORTEX_RATIO_1_22",
        }
        return canonical_json_sha256(spec_dict)

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: Collection[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate all 30-combo V3_LAB_10 signals strictly from closed OHLCV bars."""
        base_signals = super().generate_signals(
            data=data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )

        eth_df = data.get(C13_SYMBOL)
        btc_df = data.get(BTC_C4_SYMBOL)

        # Lab 10 (v3_lab f8b390f): C13 on ETH, C4 and C7 on BTC. Lab 11 (v3_lab 166aafd): BTC C1 ATR last.
        c13_filter = C13B1VolRegimeFilter.from_m15_dataframe(eth_df) if eth_df is not None and not eth_df.empty else None
        btc_c4_filter = BtcC4B1VolConfirmFilter.from_m15_dataframe(btc_df) if btc_df is not None and not btc_df.empty else None
        btc_c7_filter = BtcC7B1NoChase1HFilter.from_m15_dataframe(btc_df) if btc_df is not None and not btc_df.empty else None

        out: list[Signal] = []
        for sig in base_signals:
            dec = None
            if c13_filter is not None and sig.symbol == C13_SYMBOL and int(sig.combo_id) == C13_COMBO_ID:
                dec = c13_filter.evaluate_signal(sig)
            elif btc_c4_filter is not None and sig.symbol == BTC_C4_SYMBOL and int(sig.combo_id) == BTC_C4_COMBO_ID:
                dec = btc_c4_filter.evaluate_signal(sig)
            elif btc_c7_filter is not None and sig.symbol == BTC_C7_SYMBOL and int(sig.combo_id) == BTC_C7_COMBO_ID:
                dec = btc_c7_filter.evaluate_signal(sig)
            if dec is not None and dec.action != "KEEP":
                if only_approved:
                    continue
                sig = Signal(
                    **{
                        **sig.__dict__,
                        "approved": False,
                        "rejection_reason": dec.reason,
                    }
                )
            out.append(sig)

        # Lab 11 (V3_LAB_11_PRUNE23): BTC-USDT/C1 H2_ATR_1.00 on every BTC C1 signal (both sides).
        if btc_df is not None and not btc_df.empty:
            c1_filter = BtcC1H2Atr100Filter.from_m15_dataframe(btc_df)
            filtered: list[Signal] = []
            for sig in out:
                if sig.symbol == BTC_C1_SYMBOL and int(sig.combo_id) == BTC_C1_COMBO_ID:
                    dec = c1_filter.evaluate_signal(sig)
                    if dec.action != "KEEP":
                        if only_approved:
                            continue
                        sig = Signal(**{**sig.__dict__, "approved": False, "rejection_reason": dec.reason})
                filtered.append(sig)
            out = filtered
        return out
