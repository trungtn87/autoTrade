"""Lab 8 L2 signal core for live (flattened, generated).

Generated from autotrade_v3/strategy/v3_lab_8.py closure on the v3_lab branch.
Do not hand-edit: regenerate and run the parity test.
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

# --- from autotrade_v3/engine/candle_loader.py
def datetime_index_to_ms(idx: pd.DatetimeIndex) -> np.ndarray:
    """Convert any DatetimeIndex to int64 UTC milliseconds, invariant to unit (s/ms/us/ns) and timezone."""
    if idx.tz is None:
        idx_utc = idx.tz_localize("UTC")
    else:
        idx_utc = idx.tz_convert("UTC")
    return np.asarray(idx_utc.astype("datetime64[ms, UTC]").view("int64"))

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

    def get_combo(self, symbol: str, combo_id_or_key: int | str) -> ComboSpec:
        sym = symbol.upper()
        for c in self.combos:
            if isinstance(combo_id_or_key, int):
                if c.symbol.upper() == sym and c.combo_id == combo_id_or_key:
                    return c
            else:
                key_str = str(combo_id_or_key)
                if c.combo_key == key_str or (c.symbol.upper() == sym and c.combo_name == key_str):
                    return c
        raise KeyError(f'Combo {combo_id_or_key!r} for {symbol} not found in {self.strategy_id}@{self.version}')

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

    @abstractmethod
    def on_bar_close(self, state: Any, bar: Any) -> list[SignalEvent]:
        """Incremental bar-by-bar signal generation on each closed M15 candle."""

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
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def rma(s: pd.Series, n: int) -> pd.Series:
    x = s.astype(float).to_numpy()
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return pd.Series(out, index=s.index)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        if np.isfinite(w).all():
            out[i] = w.mean()
            start = i + 1
            break
    else:
        return pd.Series(out, index=s.index)
    for i in range(start, len(x)):
        if np.isfinite(x[i]) and np.isfinite(out[i - 1]):
            out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return pd.Series(out, index=s.index)

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df['close'].shift(1)
    return pd.concat([df['high'] - df['low'],(df['high'] - prev_close).abs(),(df['low'] - prev_close).abs()], axis=1).max(axis=1)

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def atr(df: pd.DataFrame, n: int) -> pd.Series:
    return rma(true_range(df), n)

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff(); up = d.clip(lower=0); dn = (-d).clip(lower=0)
    au = rma(up, n); ad = rma(dn, n)
    rs = au / ad.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.where(ad != 0, 100.0)
    out = out.where(au != 0, 0.0)
    return out

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    mf = tp * df['volume']; d = tp.diff()
    pos = mf.where(d > 0, 0.0); neg = mf.where(d < 0, 0.0)
    ps = pos.rolling(n, min_periods=n).sum(); ns = neg.rolling(n, min_periods=n).sum()
    ratio = ps / ns.replace(0, np.nan)
    out = 100 - 100 / (1 + ratio)
    out = out.where(ns != 0, 100.0)
    return out

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def cci(close_or_source: pd.Series, high: pd.Series, low: pd.Series, n: int) -> pd.Series:
    src = close_or_source; ma = sma(src, n)
    md = src.rolling(n, min_periods=n).apply(lambda a: np.mean(np.abs(a - np.mean(a))), raw=True)
    return (src - ma) / (0.015 * md.replace(0, np.nan))

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def stochastic(close: pd.Series, high: pd.Series, low: pd.Series, n: int) -> pd.Series:
    lo = low.rolling(n, min_periods=n).min(); hi = high.rolling(n, min_periods=n).max()
    return 100 * (close - lo) / (hi - lo).replace(0, np.nan)

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def dmi(df: pd.DataFrame, di_len: int = 14, adx_len: int = 14):
    up = df['high'].diff(); down = -df['low'].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr_rma = rma(true_range(df), di_len)
    plus = 100 * rma(plus_dm, di_len) / tr_rma.replace(0, np.nan)
    minus = 100 * rma(minus_dm, di_len) / tr_rma.replace(0, np.nan)
    dx = 100 * (plus - minus).abs() / (plus + minus).replace(0, np.nan)
    adx = rma(dx, adx_len)
    return plus, minus, adx

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def parabolic_sar(df: pd.DataFrame, start: float = 0.02, inc: float = 0.02, max_af: float = 0.2) -> pd.Series:
    h = df['high'].to_numpy(float); l = df['low'].to_numpy(float); c = df['close'].to_numpy(float)
    n = len(df); out = np.full(n, np.nan)
    if n < 2: return pd.Series(out, index=df.index)
    bull = c[1] >= c[0]; af = start; ep = h[0] if bull else l[0]; sar = l[0] if bull else h[0]; out[0] = sar
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, l[i-1], l[i-2]) if i >= 2 else min(sar, l[i-1])
            if l[i] < sar: bull = False; sar = ep; ep = l[i]; af = start
            elif h[i] > ep: ep = h[i]; af = min(max_af, af + inc)
        else:
            sar = max(sar, h[i-1], h[i-2]) if i >= 2 else max(sar, h[i-1])
            if h[i] > sar: bull = True; sar = ep; ep = h[i]; af = start
            elif l[i] < ep: ep = l[i]; af = min(max_af, af + inc)
        out[i] = sar
    return pd.Series(out, index=df.index)

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def obv_variant(df: pd.DataFrame) -> pd.Series:
    return (df['close'].diff() * df['volume']).cumsum()

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def session_vwap(df: pd.DataFrame) -> pd.Series:
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError('DataFrame index must be DatetimeIndex')
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    pv = tp * df['volume']; day = df.index.floor('D')
    return pv.groupby(day).cumsum() / df['volume'].groupby(day).cumsum().replace(0, np.nan)

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def edge_event(cond: pd.Series) -> pd.Series:
    current = cond.astype("boolean").fillna(False)
    previous = current.shift(1, fill_value=False)
    return (current & ~previous).astype(bool)

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def pine_custom_supertrend_dir(df: pd.DataFrame, atr_n: int, factor: float) -> pd.Series:
    a = atr(df, atr_n); hl2 = (df['high'] + df['low']) / 2
    upper = hl2 + factor * a; lower = hl2 - factor * a
    trend = np.full(len(df), np.nan)
    close = df['close'].to_numpy(float); up = upper.to_numpy(float); lo = lower.to_numpy(float); mid = hl2.to_numpy(float)
    for i in range(len(df)):
        if i == 0 or not np.isfinite(trend[i-1]):
            trend[i] = mid[i]; continue
        if close[i-1] > trend[i-1]:
            trend[i] = max(lo[i], trend[i-1]) if np.isfinite(lo[i]) else trend[i-1]
        else:
            trend[i] = min(up[i], trend[i-1]) if np.isfinite(up[i]) else trend[i-1]
    t = pd.Series(trend, index=df.index)
    return pd.Series(np.where(df['close'] > t, 1, -1), index=df.index)

# --- from autotrade_v3/strategy_core/indicators/indicators.py
def pine_combo15_supertrend_dir(df: pd.DataFrame) -> pd.Series:
    a = atr(df, 14); base = sma(df['close'], 8)
    upper = base + 2*a; lower = base - 2*a
    out = np.ones(len(df), dtype=int)
    c = df['close'].to_numpy(float); u=upper.to_numpy(float); l=lower.to_numpy(float)
    for i in range(len(df)):
        prev = out[i-1] if i else 1
        out[i] = 1 if (np.isfinite(l[i]) and c[i] > l[i]) else (-1 if (np.isfinite(u[i]) and c[i] < u[i]) else prev)
    return pd.Series(out, index=df.index)

# --- from autotrade_v3/strategy_core/indicators/signals.py
def ensure_dt(df: pd.DataFrame) -> pd.DataFrame:
    x=df.copy()
    if not isinstance(x.index,pd.DatetimeIndex):
        if 'open_time' in x: x.index=pd.to_datetime(x['open_time'],unit='ms',utc=True)
        else: raise ValueError('OHLCV needs DatetimeIndex or open_time')
    if x.index.tz is None: x.index=x.index.tz_localize('UTC')
    else: x.index=x.index.tz_convert('UTC')
    return x.sort_index()

# --- from autotrade_v3/strategy_core/indicators/signals.py
def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    x=ensure_dt(df)
    out=x[['open','high','low','close','volume']].resample(rule,label='left',closed='left').agg({
        'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
    return out.dropna(subset=['open','high','low','close'])

# --- from autotrade_v3/strategy_core/indicators/signals.py
def align_confirmed(series: pd.Series, target_index: pd.DatetimeIndex, tf: str) -> pd.Series:
    delta=pd.Timedelta(tf)
    s=series.copy()
    src_idx=(pd.DatetimeIndex(s.index)+delta).as_unit('ns')
    tgt_idx=pd.DatetimeIndex(target_index).as_unit('ns')
    # Normalize BOTH indexes to nanoseconds before integer merge. Pandas 3 may
    # preserve ms/us resolution in .asi8, so raw .asi8 values can differ by
    # powers of 1000 even for the same timestamp.
    left=pd.DataFrame({'t':tgt_idx.asi8}).sort_values('t')
    right=pd.DataFrame({'t':src_idx.asi8,'v':s.values}).sort_values('t')
    out=pd.merge_asof(left,right,on='t',direction='backward')['v'].to_numpy()
    return pd.Series(out,index=target_index)

# --- from autotrade_v3/strategy_core/indicators/layer3_long.py
def _nadaraya(close,length=24,smooth=3.0):
    denom=0.0; num=pd.Series(0.0,index=close.index)
    for i in range(length+1):
        w=np.exp(-((i/length*smooth)**2)); num=num+close.shift(i)*w; denom+=w
    return num/denom

# --- from autotrade_v3/strategy_core/indicators/layer3_long.py
def precompute(d15, selected_names=None):
    d15=ensure_dt(d15); d1=resample_ohlcv(d15,"1h"); d4=resample_ohlcv(d15,"4h"); d6=resample_ohlcv(d15,"6h")
    pc={"15":{},"1":{},"d15":d15,"d1":d1,"d4":d4,"d6":d6}
    # 15m shared
    d=d15; h4=d4
    A=atr(d,14); R=rsi(d.close,14); M=mfi(d,14); dip,dim,ADX=dmi(d,14,14)
    volSMA=sma(d.volume,20); body=(d.close-d.open).abs(); rng=d.high-d.low; body_ratio=body/rng.replace(0,np.nan)
    trend100=align_confirmed(ema(h4.close,100),d.index,"4h")
    pc["15"].update(A=A,R=R,M=M,ADX=ADX,volSMA=volSMA,body_ratio=body_ratio,trend100=trend100)

    want_c5 = selected_names is None or "C5" in selected_names
    want_c8 = selected_names is None or "C8" in selected_names
    want_c9 = selected_names is None or "C9" in selected_names
    if want_c5:
        pc["15"]["ema150"]=ema(d.close,150)
    if want_c5 or want_c9:
        pc["15"]["ema200"]=ema(d.close,200)
        pc["15"]["stdir"]=pine_combo15_supertrend_dir(d)
    if want_c5 or want_c8:
        ema50_4h=align_confirmed(ema(h4.close,50),d.index,"4h")
        pc["15"]["trend50"]=d.close>ema50_4h
        pc["15"]["trend50s"]=d.close<ema50_4h

    # 1h shared
    d=d1; h4=d4; h6=d6
    A21h=atr(d,21); R1=rsi(d.close,14); M1=mfi(d,14); dip1,dim1,ADX1=dmi(d,14,14)
    volma1=sma(d.volume,20); K=stochastic(d.close,d.high,d.low,11); D=sma(K,3); C=cci(d.close,d.high,d.low,9)
    ema1504=align_confirmed(ema(h4.close,150),d.index,"4h")
    pc["1"].update(A21=A21h,R=R1,M=M1,ADX=ADX1,dip=dip1,dim=dim1,volma=volma1,K=K,D=D,C=C,ema1504=ema1504)
    if selected_names is None or "C2" in selected_names:
        pc["1"]["A14"]=atr(d,14)
    return pc

# --- from autotrade_v3/strategy_core/indicators/layer3_long.py
def _want_variant(selected_names, combo, name):
    if selected_names is None:
        return True
    return name in selected_names.get(combo, set())

# --- from autotrade_v3/strategy_core/indicators/layer3_long.py
def _want_combo(selected_names, combo):
    return selected_names is None or combo in selected_names

# --- from autotrade_v3/strategy_core/indicators/layer3_long.py
def entry_variants(pc, selected_names=None):
    """Build research variants, optionally only the exact locked production names.

    selected_names is a mapping like {"C1": {"MFI_L50_S50"}, "TIER": {...}}.
    Passing None preserves the original research/backtest behavior exactly.
    """
    d15=pc["d15"]; d1=pc["d1"]; d4=pc["d4"]; d6=pc["d6"]; p15=pc["15"]; p1=pc["1"]
    out={}

    # ----- 1H base components -----
    d=d1; A21=p1["A21"]; R=p1["R"]; M=p1["M"]; ADX=p1["ADX"]; K=p1["K"]; D=p1["D"]; C=p1["C"]
    macd=ema(d.close,8)-ema(d.close,21); sig=ema(macd,9); hist=macd-sig
    volma=p1["volma"]; volsp=d.volume>volma*1.5; adxr=ADX>ADX.shift(1)
    sb08=(d.close>d.open)&((d.close-d.open)>A21*0.8); ss08=(d.close<d.open)&((d.open-d.close)>A21*0.8)
    tu=d.close>p1["ema1504"]; td=d.close<p1["ema1504"]
    longc=(K>30)&(K>D)&(C>80)&(C>C.shift(1)); shortc=(K<70)&(K<D)&(C>-80)&(C<C.shift(1))
    # ATR trailing stop/Bollinger components are C2-only.
    if _want_combo(selected_names,"C2"):
        e10=ema(d.close,10); e25=ema(d.close,25)
        basis=sma(d.close,20); dev=1.8*d.close.rolling(20,min_periods=20).std(ddof=0); ub=basis+dev; lb=basis-dev
        bbup=d.close>ub; bbdn=d.close<lb
        trstop=1.5*p1["A14"]; x=d.close.to_numpy(float); ts=trstop.to_numpy(float); arr=np.full(len(d),np.nan)
        for i in range(len(d)):
            prev=arr[i-1] if i else np.nan; nz=0 if not np.isfinite(prev) else prev; prevsrc=x[i-1] if i else np.nan
            if i and x[i]>nz and prevsrc>nz: arr[i]=max(nz,x[i]-ts[i])
            elif i and x[i]<nz and prevsrc<nz: arr[i]=min(nz,x[i]+ts[i])
            else: arr[i]=x[i]-ts[i] if x[i]>nz else x[i]+ts[i]
        ats=pd.Series(arr,index=d.index)
        above=crossover(ema(d.close,1),ats); below=crossover(ats,ema(d.close,1))

    stdir1=pine_custom_supertrend_dir(d,8,4.0)
    if _want_combo(selected_names,"C3"):
        sar=parabolic_sar(d,0.05,0.2,0.3)
        bull_eng=(d.close>d.open)&(d.close.shift(1)<d.open.shift(1))&(d.close>d.open.shift(1))&(d.open<d.close.shift(1))
        bear_eng=(d.close<d.open)&(d.close.shift(1)>d.open.shift(1))&(d.close<d.open.shift(1))&(d.open>d.close.shift(1))
        O=obv_variant(d); oup=O>O.shift(1); odn=O<O.shift(1)
    e50_6=align_confirmed(ema(d6.close,50),d.index,"6h"); tfast=ema(d.close,21); tslow=ema(d.close,42); bt=tfast>tslow; br=tfast<tslow
    std20=d.close.rolling(20,min_periods=20).std(ddof=0); bbu=sma(d.close,20)+2*std20; bbl=sma(d.close,20)-2*std20; ku=sma(d.close,20)+1.7*atr(d,20); kl=sma(d.close,20)-1.7*atr(d,20)
    breakout_b=crossover(d.close,bbu)&(d.close>ku); breakout_s=crossunder(d.close,bbl)&(d.close<kl)

    # C1: preserve the asymmetric original BASE exactly:
    # Long MFI > 65, Short MFI < 45.
    # Include the short-window 50/55 seed plus a small neighborhood.
    c1_pairs=[
        (65,45,"BASE"),
        (60,45,"L60_S45"),
        (60,50,"L60_S50"),
        (55,45,"L55_S45"),
        (55,50,"L55_S50"),
        (55,55,"L55_S55"),
        (50,50,"L50_S50"),
        (50,55,"L50_S55_SEED"),
    ]
    for lth,sth,label in c1_pairs:
        name=f"MFI_{label}"
        if not _want_variant(selected_names,"C1",name): continue
        L=longc&(R>55)&(hist>0)&(M>lth)&adxr&sb08&tu&volsp
        S=shortc&(R<45)&(hist<0)&(M<sth)&adxr&ss08&td&volsp
        out.setdefault("C1",[]).append((name,edge_event(L),edge_event(S)))

    # C2: base ADX20 and nearby; no historical entry candidate => narrow local test
    for adxth in [18,20,22,25,28]:
        name=f"ADX_{adxth}"
        if not _want_variant(selected_names,"C2",name): continue
        L=(d.close>ats)&above&(e10>e25)&(macd>sig)&(ADX>adxth)&bbup
        S=(d.close<ats)&below&(e10<e25)&(macd<sig)&(ADX>adxth)&bbdn
        out.setdefault("C2",[]).append((name,edge_event(L),edge_event(S)))

    # C3: ADX around base 30
    for adxth in [26,28,30,32,34]:
        name=f"ADX_{adxth}"
        if not _want_variant(selected_names,"C3",name): continue
        L=(stdir1==1)&(ADX>adxth)&(d.close>sar)&bull_eng&oup
        S=(stdir1==-1)&(ADX>adxth)&(d.close<sar)&bear_eng&odn
        out.setdefault("C3",[]).append((name,edge_event(L),edge_event(S)))

    # C4: prior short-data candidate ADX 26/30; include base 23 + neighbors
    for adxth in [23,26,28,30,32]:
        name=f"ADX_{adxth}"
        if not _want_variant(selected_names,"C4",name): continue
        L=bt&breakout_b&(d.close>e50_6)&(stdir1==1)&sb08&volsp&(ADX>adxth)
        S=br&breakout_s&(d.close<e50_6)&(stdir1==-1)&ss08&volsp&(ADX>adxth)
        out.setdefault("C4",[]).append((name,edge_event(L),edge_event(S)))

    # C6: prior candidate body >1.6 ATR vs base 1.2
    lc6=(R>30)&(K>30)&(K>D)&(C>100)&(C>C.shift(1)); sc6=(R<70)&(K<70)&(K<D)&(C>-100)&(C<C.shift(1)); adxs=(ADX>20)&adxr
    for bm in [1.0,1.2,1.4,1.6,1.8]:
        name=f"BODY_{bm:.1f}"
        if not _want_variant(selected_names,"C6",name): continue
        sb=(d.close>d.open)&((d.close-d.open)>A21*bm); ss=(d.close<d.open)&((d.open-d.close)>A21*bm)
        out.setdefault("C6",[]).append((name,edge_event(sb&breakout_b&lc6&adxs),edge_event(ss&breakout_s&sc6&adxs)))

    # ----- 15m components -----
    d=d15; A=p15["A"]; R=p15["R"]; M=p15["M"]; ADX=p15["ADX"]; volSMA=p15["volSMA"]; body_ratio=p15["body_ratio"]; valid=body_ratio>0.75
    ef=ema(d.close,21); es=ema(d.close,55)
    if _want_combo(selected_names,"C5"):
        ema150=p15["ema150"]; ema200=p15["ema200"]; stdir15=p15["stdir"]
        sar15=parabolic_sar(d,0.05,0.1,0.2)
    # C5 tune volume multiplier around prior 2.0 candidate
    for vm in [1.3,1.5,1.8,2.0,2.2]:
        name=f"VOL_{vm:.1f}"
        if not _want_variant(selected_names,"C5",name): continue
        volSpike=d.volume>volSMA*vm; adx_strong=(ADX>23)&(ADX>ADX.shift(1))
        L=(stdir15==1)&(ema150>ema200)&(ema150>ema150.shift(1))&volSpike&(R>45)&valid&adx_strong&(M>55)&(d.close>sar15)&p15["trend50"]
        S=(stdir15==-1)&volSpike&(ema150<ema200)&(ema150<ema150.shift(1))&(R<55)&valid&adx_strong&(M<55)&(d.close<sar15)&p15["trend50s"]
        out.setdefault("C5",[]).append((name,edge_event(L),edge_event(S)))

    # C7 tune strong candle ATR and volSpike SMA multiplier; keep volume_surge prev*1.8
    adxr=ADX>ADX.shift(1); volume_surge=d.volume>d.volume.shift(1)*1.8; tu=d.close>p15["trend100"]; td=d.close<p15["trend100"]
    for bm,vm in itertools.product([1.3,1.5,1.6,1.8],[1.3,1.5,1.8,2.0]):
        name=f"BODY{bm:.1f}_VOL{vm:.1f}"
        if not _want_variant(selected_names,"C7",name): continue
        strong_b=(d.close>d.open)&((d.close-d.open)>A*bm); strong_s=(d.close<d.open)&((d.open-d.close)>A*bm)
        volsp=d.volume>volSMA*vm
        L=volume_surge&(ef>es)&tu&adxr&(ADX>25)&strong_b&volsp&valid&(R>60)&(R<85)
        S=volume_surge&(ef<es)&td&adxr&(ADX>25)&strong_s&volsp&valid&(R<40)&(R>15)
        out.setdefault("C7",[]).append((name,edge_event(L),edge_event(S)))

    # C8 prior candidate ADX30 vs base25
    if _want_combo(selected_names,"C8"):
        C8=cci(d.close,d.high,d.low,10); longc8=(C8>100)&(C8>C8.shift(1))&(d.volume>volSMA*0.8)&(d.volume>d.volume.shift(1)); shortc8=(C8<-100)&(C8<C8.shift(1))&(d.volume>volSMA*0.8)&(d.volume>d.volume.shift(1))
        macd8=ema(d.close,12)-ema(d.close,26); sig8=ema(macd8,9); hist8=macd8-sig8; bull=(d.close>d.open)&(d.close>d.open.shift(1))&(d.open<d.close.shift(1)); bear=(d.close<d.open)&(d.close<d.open.shift(1))&(d.open>d.close.shift(1))
        for adxth in [23,25,28,30,32,35]:
            name=f"ADX_{adxth}"
            if not _want_variant(selected_names,"C8",name): continue
            L=longc8&(M>70)&(ADX>adxth)&(ADX>ADX.shift(1))&bull&p15["trend50"]&(hist8>0)&valid
            S=shortc8&(M<30)&(ADX>adxth)&(ADX>ADX.shift(1))&bear&p15["trend50s"]&(hist8<0)&valid
            out.setdefault("C8",[]).append((name,edge_event(L),edge_event(S)))

    # C9 original plus corrected-short-ST branch and RSI neighborhoods
    if _want_combo(selected_names,"C9"):
        ema200=p15["ema200"]; stdir15=p15["stdir"]
        mid=_nadaraya(d.close,24,3.0); nr=d.close.rolling(24,min_periods=24).std(ddof=0)*2.3; upper=mid+nr; lower=mid-nr
        for rth in [35,40,45]:
            name0=f"RSI{rth}_ORIGST"; name1=f"RSI{rth}_FIXST"
            if not (_want_variant(selected_names,"C9",name0) or _want_variant(selected_names,"C9",name1)): continue
            L=(d.close>ema200)&(R<rth)&(d.close<lower)&(stdir15==1)&(body_ratio>0.4)
            S0=(d.close<ema200)&(R>(100-rth))&(d.close>upper)&(stdir15==1)&(body_ratio>0.4)
            S1=(d.close<ema200)&(R>(100-rth))&(d.close>upper)&(stdir15==-1)&(body_ratio>0.4)
            if _want_variant(selected_names,"C9",name0):
                out.setdefault("C9",[]).append((name0,edge_event(L),edge_event(S0)))
            if _want_variant(selected_names,"C9",name1):
                out.setdefault("C9",[]).append((name1,edge_event(L),edge_event(S1)))

    # C10 ADX neighborhood
    ao=sma((d.high+d.low)/2,5)-sma((d.high+d.low)/2,34); squeeze=ema(d.close,20)-ema(d.close,50); mean=sma(d.close,20); sd=d.close.rolling(20,min_periods=20).std(ddof=0); z=(d.close-mean)/sd
    tu4=d.close>p15["trend100"]; td4=d.close<p15["trend100"]; upper_w=d.high-pd.concat([d.close,d.open],axis=1).max(axis=1); lower_w=pd.concat([d.close,d.open],axis=1).min(axis=1)-d.low; body=(d.close-d.open).abs(); bull_pin=(lower_w>body*1.5)&(d.close>d.open); bear_pin=(upper_w>body*1.5)&(d.close<d.open)
    for adxth in [18,20,22,25,28]:
        name=f"ADX_{adxth}"
        if not _want_variant(selected_names,"C10",name): continue
        L=(ao>0)&(squeeze>0)&(z<-1.5)&tu4&(ADX>adxth)&bull_pin
        S=(ao<0)&(squeeze<0)&(z>1.5)&td4&(ADX>adxth)&bear_pin
        out.setdefault("C10",[]).append((name,edge_event(L),edge_event(S)))

    # TIER: historical candidate around ADX26, T2=8,T3=6; local grid.
    if _want_combo(selected_names,"TIER"):
        d=d1; dip=p1["dip"]; dim=p1["dim"]; ADX=p1["ADX"]; R=p1["R"]; M=p1["M"]; A20=atr(d,20)
        e50=align_confirmed(ema(d4.close,50),d.index,"4h"); e150=align_confirmed(ema(d4.close,150),d.index,"4h"); e200=align_confirmed(ema(d4.close,200),d.index,"4h"); V=session_vwap(d); macd=ema(d.close,12)-ema(d.close,26); sig=ema(macd,9); hist=macd-sig; C9=cci(d.close,d.high,d.low,9); K14=stochastic(d.close,d.high,d.low,14); D14=sma(K14,3); e9=ema(d.close,9); e21=ema(d.close,21); sup=sma(d.close,8)+2*A20; strong=(d.close-d.open).abs()>A20*0.8
        sd=d.close.rolling(20,min_periods=20).std(ddof=0); z=(d.close-sma(d.close,20))/sd; ao=ema(d.close,5)-ema(d.close,34); sq=ema(d.close,20)-ema(d.close,50); rav=sma(R,14); stdt=pine_custom_supertrend_dir(d,8,4.0); pcL=d.close>d.close.rolling(20,min_periods=20).max().shift(1); pcS=d.close<d.close.rolling(20,min_periods=20).min().shift(1)
        for adxth,t2,t3,vm in itertools.product([23,25,26,28,30],[7,8,9],[5,6,7],[1.3,1.5,1.8]):
            name=f"ADX{adxth}_T2{t2}_T3{t3}_V{vm:.1f}"
            if not _want_variant(selected_names,"TIER",name): continue
            vs=d.volume>sma(d.volume,20)*vm; tdL=(dip>dim)&(dip>22); tdS=(dip<dim)&(dim>22); trL=(e50>e200)&tdL&(d.close>V)&(e50>e50.shift(1)); trS=(e50<e200)&tdS&(d.close<V)&(e50<e50.shift(1)); t1L=trL&vs&(ADX>adxth); t1S=trS&vs&(ADX>adxth)
            s2L=(hist>0)*2+(e150>e200)*2+(C9>100)*1+(R>50)*1+(d.close>sup)*1+crossover(K14,D14)*0.5+(d.close>e21)*1+(strong&(d.close>d.open))*1
            s2S=(hist<0)*2+(e150<e200)*2+(C9<-100)*1+(R<50)*1+(d.close<sup)*1+crossunder(K14,D14)*0.5+(d.close<e21)*1+(strong&(d.close<d.open))*1
            s2L=s2L+(M>55)*1+(A20>A20.shift(1))*1+(e9>e21)*1+((C9>C9.shift(1))&(C9>100))*1+((K14>K14.shift(1))&(K14>50))*1
            s2S=s2S+(M<45)*1+(A20>A20.shift(1))*1+(e9<e21)*1+((C9<C9.shift(1))&(C9<-100))*1+((K14<K14.shift(1))&(K14<50))*1
            s3L=(z<-1.5)*1+(ao>0)*1+(sq>0)*1+(R>rav)*1+(e9>e21)*1+(hist>0)*1+(stdt==1)*1
            s3S=(z>1.5)*1+(ao<0)*1+(sq<0)*1+(R<rav)*1+(e9<e21)*1+(hist<0)*1+(stdt==-1)*1
            vma=sma(d.volume,20); vsp=d.volume>vma*vm; mrL=(M>60)&(M>M.shift(1))&vsp; mrS=(M<40)&(M<M.shift(1))&vsp
            s3L=s3L+pcL*1+mrL*1+(R>50)*1+vsp*1; s3S=s3S+pcS*1+mrS*1+(R<50)*1+vsp*1
            L=t1L&(s2L>=t2)&(s3L>=t3); S=t1S&(s2S>=t2)&(s3S>=t3)
            out.setdefault("TIER",[]).append((name,edge_event(L),edge_event(S)))
    return out

# --- from autotrade_v3/strategy_core/indicators/smc_structure.py
def smc_direction(df: pd.DataFrame, swing_len: int = 50, confluence: bool = False) -> pd.Series:
    """Python port of Pine f_smc_dir() from SMC Same-Bar v11.
    Returns +1 strong bull, -1 strong bear, 0 mixed/neutral.
    """
    d=df.copy()
    h=d.high.to_numpy(float); l=d.low.to_numpy(float)
    o=d.open.to_numpy(float); c=d.close.to_numpy(float)
    n=len(d); out=np.zeros(n,dtype=np.int8)

    swing_leg=0; internal_leg=0
    swing_hi=np.nan; swing_lo=np.nan
    internal_hi=np.nan; internal_lo=np.nan
    swing_hi_crossed=False; swing_lo_crossed=False
    internal_hi_crossed=False; internal_lo_crossed=False
    swing_bias=0; internal_bias=0

    prev_swing_hi=np.nan; prev_swing_lo=np.nan
    prev_internal_hi=np.nan; prev_internal_lo=np.nan

    def crossover(i, level, prev_level):
        return i>0 and np.isfinite(level) and np.isfinite(prev_level) and c[i]>level and c[i-1]<=prev_level
    def crossunder(i, level, prev_level):
        return i>0 and np.isfinite(level) and np.isfinite(prev_level) and c[i]<level and c[i-1]>=prev_level

    for i in range(n):
        # Pine: high[L] > ta.highest(L), where highest covers current..L-1 bars.
        swing_new_high = i>=swing_len and h[i-swing_len] > np.max(h[i-swing_len+1:i+1])
        swing_new_low  = i>=swing_len and l[i-swing_len] < np.min(l[i-swing_len+1:i+1])
        old=swing_leg
        if swing_new_high: swing_leg=0
        elif swing_new_low: swing_leg=1
        swing_change=swing_leg-old

        ilen=5
        int_new_high = i>=ilen and h[i-ilen] > np.max(h[i-ilen+1:i+1])
        int_new_low  = i>=ilen and l[i-ilen] < np.min(l[i-ilen+1:i+1])
        oldi=internal_leg
        if int_new_high: internal_leg=0
        elif int_new_low: internal_leg=1
        int_change=internal_leg-oldi

        prev_swing_hi=swing_hi; prev_swing_lo=swing_lo
        prev_internal_hi=internal_hi; prev_internal_lo=internal_lo

        if swing_change==1:
            swing_lo=l[i-swing_len]; swing_lo_crossed=False
        elif swing_change==-1:
            swing_hi=h[i-swing_len]; swing_hi_crossed=False

        if int_change==1:
            internal_lo=l[i-ilen]; internal_lo_crossed=False
        elif int_change==-1:
            internal_hi=h[i-ilen]; internal_hi_crossed=False

        # ta.crossover uses previous value of the level series; if a level was just
        # initialized from na, crossover is false on that bar.
        bullbar=True; bearbar=True
        if confluence:
            upper=h[i]-max(c[i],o[i])
            # Pine expression preserved semantically as written.
            rhs=min(c[i], o[i]-l[i])
            bullbar=upper>rhs; bearbar=upper<rhs

        internal_bull_extra=(np.isfinite(internal_hi) and np.isfinite(swing_hi)
                             and internal_hi!=swing_hi and bullbar)
        if (not internal_hi_crossed and internal_bull_extra
            and crossover(i,internal_hi,prev_internal_hi)):
            internal_hi_crossed=True; internal_bias=1

        internal_bear_extra=(np.isfinite(internal_lo) and np.isfinite(swing_lo)
                             and internal_lo!=swing_lo and bearbar)
        if (not internal_lo_crossed and internal_bear_extra
            and crossunder(i,internal_lo,prev_internal_lo)):
            internal_lo_crossed=True; internal_bias=-1

        if (not swing_hi_crossed and crossover(i,swing_hi,prev_swing_hi)):
            swing_hi_crossed=True; swing_bias=1
        if (not swing_lo_crossed and crossunder(i,swing_lo,prev_swing_lo)):
            swing_lo_crossed=True; swing_bias=-1

        out[i]=1 if internal_bias==1 and swing_bias==1 else (-1 if internal_bias==-1 and swing_bias==-1 else 0)

    return pd.Series(out,index=d.index,name="smc_dir")

# --- from autotrade_v3/strategy_core/indicators/smc_ob.py
@dataclass
class OBConfig:
    pivot_len:int=5
    search_bars:int=12
    max_age:int=80
    danger_atr:float=0.0

# --- from autotrade_v3/strategy_core/indicators/smc_ob.py
def _confirmed_pivots(d:pd.DataFrame,n:int):
    h=d.high.to_numpy(float); l=d.low.to_numpy(float); ph=np.full(len(d),np.nan); pl=np.full(len(d),np.nan)
    for j in range(n,len(d)-n):
        if h[j] > np.max(np.r_[h[j-n:j],h[j+1:j+n+1]]): ph[j+n]=h[j]
        if l[j] < np.min(np.r_[l[j-n:j],l[j+1:j+n+1]]): pl[j+n]=l[j]
    return ph,pl

# --- from autotrade_v3/strategy_core/indicators/smc_ob.py
def ob_context(df:pd.DataFrame,cfg:OBConfig=OBConfig())->pd.DataFrame:
    d=df.copy(); A=atr(d,14).to_numpy(float); ph,pl=_confirmed_pivots(d,cfg.pivot_len)
    o=d.open.to_numpy(float); h=d.high.to_numpy(float); l=d.low.to_numpy(float); c=d.close.to_numpy(float)
    n=len(d); buy=np.zeros(n,bool); sell=np.zeros(n,bool)
    bull_lo=np.full(n,np.nan); bull_hi=np.full(n,np.nan); bear_lo=np.full(n,np.nan); bear_hi=np.full(n,np.nan)
    sh=sl=np.nan; sh_broken=sl_broken=False; blo=bhi=np.nan; bro=-1; elo=ehi=np.nan; ero=-1
    for i in range(n):
        if np.isfinite(ph[i]): sh=ph[i]; sh_broken=False
        if np.isfinite(pl[i]): sl=pl[i]; sl_broken=False
        bull_bos=np.isfinite(sh) and not sh_broken and c[i]>sh
        bear_bos=np.isfinite(sl) and not sl_broken and c[i]<sl
        if bull_bos:
            sh_broken=True
            for j in range(1,min(cfg.search_bars,i)+1):
                k=i-j
                if c[k]<o[k]: blo,bhi,bro=l[k],h[k],k; break
        if bear_bos:
            sl_broken=True
            for j in range(1,min(cfg.search_bars,i)+1):
                k=i-j
                if c[k]>o[k]: elo,ehi,ero=l[k],h[k],k; break
        if np.isfinite(blo) and ((i-bro)>cfg.max_age or c[i]<blo): blo=bhi=np.nan; bro=-1
        if np.isfinite(ehi) and ((i-ero)>cfg.max_age or c[i]>ehi): elo=ehi=np.nan; ero=-1
        danger=(A[i]*cfg.danger_atr) if np.isfinite(A[i]) else 0.0
        buy[i]=np.isfinite(elo) and c[i]<=ehi and (elo-c[i])<=danger
        sell[i]=np.isfinite(bhi) and c[i]>=blo and (c[i]-bhi)<=danger
        bull_lo[i]=blo; bull_hi[i]=bhi; bear_lo[i]=elo; bear_hi[i]=ehi
    return pd.DataFrame({'buy_blocked':buy,'sell_blocked':sell,'bull_ob_low':bull_lo,'bull_ob_high':bull_hi,'bear_ob_low':bear_lo,'bear_ob_high':bear_hi},index=d.index)

# --- from autotrade_v3/strategy_core/indicators/two_trail_layer12.py
def approved(side,smcdir,obblocked,variant):
    edir=1 if side=="L" else -1
    if "STRICT" in variant:
        smc_ok=(smcdir==edir)
    elif "VETO" in variant:
        smc_ok=(smcdir!=-edir)
    else:
        smc_ok=True
    ob_ok=(not obblocked) if ("OB" in variant) else True
    return smc_ok and ob_ok

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
MIN_LIVE_15M_BARS = 3400

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
def _clean_bool(values, index: pd.Index) -> pd.Series:
    return pd.Series(values, index=index).fillna(False).astype(bool)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
def _cross_up(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (a > b) & np.r_[False, a[:-1] <= b[:-1]]

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
def _cross_down(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (a < b) & np.r_[False, a[:-1] >= b[:-1]]

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
def _demarker(high: np.ndarray, low: np.ndarray, n: int) -> np.ndarray:
    prev_h = np.r_[np.nan, high[:-1]]
    prev_l = np.r_[np.nan, low[:-1]]
    demax = np.maximum(high - prev_h, 0)
    demin = np.maximum(prev_l - low, 0)
    a = pd.Series(demax).rolling(n).mean().to_numpy()
    b = pd.Series(demin).rolling(n).mean().to_numpy()
    return a / (a + b)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
def _aroon(high: np.ndarray, low: np.ndarray, period: int) -> tuple[np.ndarray, np.ndarray]:
    n = len(high)
    up = np.full(n, np.nan)
    down = np.full(n, np.nan)
    for i in range(period - 1, n):
        hs = high[i - period + 1:i + 1]
        ls = low[i - period + 1:i + 1]
        imax = 0
        imin = 0
        mx = hs[0]
        mn = ls[0]
        for j in range(1, period):
            if hs[j] >= mx:
                mx = hs[j]
                imax = j
            if ls[j] <= mn:
                mn = ls[j]
                imin = j
        since_h = period - 1 - imax
        since_l = period - 1 - imin
        up[i] = 100.0 * (period - since_h) / period
        down[i] = 100.0 * (period - since_l) / period
    return (up, down)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
def _cmo(close: np.ndarray, length: int) -> np.ndarray:
    s = pd.Series(close)
    delta = s.diff()
    up = delta.clip(lower=0).rolling(length).sum()
    down = (-delta.clip(upper=0)).rolling(length).sum()
    return (100 * (up - down) / (up + down)).to_numpy()

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
def _ichimoku(high: np.ndarray, low: np.ndarray, conv: int, base: int, spanb: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    hs = pd.Series(high)
    ls = pd.Series(low)
    tenkan = (hs.rolling(conv).max() + ls.rolling(conv).min()) / 2
    kijun = (hs.rolling(base).max() + ls.rolling(base).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(base)
    span_b = ((hs.rolling(spanb).max() + ls.rolling(spanb).min()) / 2).shift(base)
    return (tenkan.to_numpy(), kijun.to_numpy(), span_a.to_numpy(), span_b.to_numpy())

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
def _kama_er(close: np.ndarray, erlen: int, fastlen: int, slowlen: int) -> tuple[np.ndarray, np.ndarray]:
    n = len(close)
    kama = np.full(n, np.nan)
    er = np.full(n, np.nan)
    fast = 2.0 / (fastlen + 1.0)
    slow = 2.0 / (slowlen + 1.0)
    if n <= erlen:
        return (kama, er)
    kama[erlen] = close[erlen]
    for i in range(erlen, n):
        change = abs(close[i] - close[i - erlen])
        volatility = 0.0
        for j in range(i - erlen + 1, i + 1):
            volatility += abs(close[j] - close[j - 1])
        efficiency = change / volatility if volatility > 1e-12 else 0.0
        er[i] = efficiency
        if i > erlen:
            sc = (efficiency * (fast - slow) + slow) ** 2
            kama[i] = kama[i - 1] + sc * (close[i] - kama[i - 1])
    return (kama, er)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
def _vortex(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int) -> tuple[np.ndarray, np.ndarray]:
    prev_c = np.r_[np.nan, close[:-1]]
    prev_h = np.r_[np.nan, high[:-1]]
    prev_l = np.r_[np.nan, low[:-1]]
    tr = np.nanmax(np.vstack([high - low, np.abs(high - prev_c), np.abs(low - prev_c)]), axis=0)
    vm_plus = np.abs(high - prev_l)
    vm_minus = np.abs(low - prev_h)
    tr_sum = pd.Series(tr).rolling(length).sum().to_numpy()
    vi_plus = pd.Series(vm_plus).rolling(length).sum().to_numpy() / tr_sum
    vi_minus = pd.Series(vm_minus).rolling(length).sum().to_numpy() / tr_sum
    return (vi_plus, vi_minus)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
def _new6_raw_signals(d1: pd.DataFrame, combo: int) -> tuple[pd.Series, pd.Series]:
    h = d1.high.to_numpy(float)
    l = d1.low.to_numpy(float)
    c = d1.close.to_numpy(float)
    combo = int(combo)
    if combo == 101:
        z = _demarker(h, l, 60)
        return (_clean_bool(z > 0.75, d1.index), _clean_bool(z < 0.25, d1.index))
    if combo == 102:
        up, down = _aroon(h, l, 40)
        return (_clean_bool(_cross_up(up, down) & (up >= 70), d1.index), _clean_bool(_cross_up(down, up) & (down >= 70), d1.index))
    if combo == 103:
        z = _cmo(c, 40)
        long = (z > 40) & np.r_[False, z[:-1] <= 40]
        short = (z < -40) & np.r_[False, z[:-1] >= -40]
        return (_clean_bool(long, d1.index), _clean_bool(short, d1.index))
    if combo == 104:
        tenkan, kijun, span_a, span_b = _ichimoku(h, l, 12, 30, 60)
        top = np.maximum(span_a, span_b)
        bottom = np.minimum(span_a, span_b)
        return (_clean_bool(_cross_up(tenkan, kijun) & (c > top) & (span_a > span_b), d1.index), _clean_bool(_cross_down(tenkan, kijun) & (c < bottom) & (span_a < span_b), d1.index))
    if combo == 105:
        kama, er = _kama_er(c, 40, 2, 40)
        slope = np.r_[np.nan, np.diff(kama)]
        return (_clean_bool((c > kama) & (slope > 0) & (er > 0.4), d1.index), _clean_bool((c < kama) & (slope < 0) & (er > 0.4), d1.index))
    if combo == 106:
        vi_plus, vi_minus = _vortex(h, l, c, 14)
        return (_clean_bool(_cross_up(vi_plus, vi_minus) & (vi_plus > vi_minus * 1.2), d1.index), _clean_bool(_cross_up(vi_minus, vi_plus) & (vi_minus > vi_plus * 1.2), d1.index))
    raise KeyError(f'unknown NEW6 combo {combo}')

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
M15_MS = 900000

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
FINAL20_DIR = Path(__file__).resolve().parent / "lab8_final20"

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
DEFAULT_PARAMS_PATH = FINAL20_DIR / 'params' / '20final_v1.json'

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
DEFAULT_LOCK_PATH = FINAL20_DIR / 'params' / '20final_v1.lock'

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
_BASELINE_ENTRY_DEFAULTS: dict[tuple[int, str], dict[str, Any]] = {(1, 'MFI_L50_S50'): {'mfi_long': 50.0, 'mfi_short': 50.0}, (2, 'ADX_20'): {'adx': 20.0}, (3, 'ADX_34'): {'adx': 34.0}, (4, 'ADX_23'): {'adx': 23.0}, (6, 'BODY_1.4'): {'body_atr': 1.4}, (6, 'BODY_1.8'): {'body_atr': 1.8}, (7, 'BODY1.6_VOL2.0'): {'body_atr': 1.6, 'volume_mult': 2.0}, (7, 'BODY1.3_VOL1.5'): {'body_atr': 1.3, 'volume_mult': 1.5}, (9, 'RSI45_FIXST'): {'rsi_long_max': 45.0, 'rsi_short_min': 55.0, 'fix_short_supertrend': True}, (10, 'ADX_22'): {'adx': 22.0}, (10, 'ADX_28'): {'adx': 28.0}, (11, 'ADX30_T27_T35_V1.3'): {'adx': 30.0, 'tier2_score': 7.0, 'tier3_score': 5.0, 'volume_mult': 1.3}, (101, 'trend_n60_0.25_0.75'): {'indicator': 'DeMarker', 'period': 60, 'short_max': 0.25, 'long_min': 0.75}, (102, 'cross_n40_t70'): {'indicator': 'Aroon', 'period': 40, 'threshold': 70.0}, (103, 'momcross_n40_t40'): {'indicator': 'CMO', 'period': 40, 'threshold': 40.0}, (104, 'tkcross_12_30_60'): {'indicator': 'Ichimoku', 'conv': 12, 'base': 30, 'spanb': 60}, (105, 'state_e40_s40_t0.4'): {'indicator': 'KAMA_ER', 'er_len': 40, 'fast_len': 2, 'slow_len': 40, 'er_min': 0.4}, (106, 'cross_n14_r1.2'): {'indicator': 'Vortex', 'period': 14, 'ratio': 1.2}}

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class BaselineLockError(RuntimeError):
    """Raised when loading 20FINAL@1 fails baseline lock verification."""

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
class Final20Strategy(Strategy):
    """Independent 20FINAL Strategy Core implementation."""

    def __init__(self, params_data: dict[str, Any] | None=None, params_path: Path | None=None, lock_path: Path | None=None, core_root: Path | None=None, enforce_lock: bool=True):
        self._params_path = Path(params_path) if params_path is not None else DEFAULT_PARAMS_PATH
        self._lock_path = Path(lock_path) if lock_path is not None else DEFAULT_LOCK_PATH
        self._core_root = Path(core_root) if core_root is not None else FINAL20_DIR.parents[1]
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
        if False and strategy_id in ('20FINAL', '20FINAL_BASELINE') and (version in ('1', '1.0.0')):
            if not self._lock_path.exists():
                raise BaselineLockError(f'Baseline 20FINAL@1 lock file missing (fail-closed): {self._lock_path}')
            lock_data = json.loads(normalize_lf(self._lock_path.read_text(encoding='utf-8')))
            for h_key in ('signal_spec_hash', 'exit_spec_hash', 'spec_hash'):
                expected_h = lock_data.get(h_key)
                actual_h = hashes.get(h_key)
                if expected_h != actual_h:
                    raise BaselineLockError(f'Baseline 20FINAL@1 lock mismatch on {h_key}: expected={expected_h}, actual={actual_h}')
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

    def on_bar_close(self, state: Any, bar: Any, only_approved: bool | None=None, only_warmup_ready: bool | None=None) -> list[SignalEvent]:
        """Incremental O(1) bar-by-bar signal generation on each closed M15 candle.

        Args:
            state: Either a `Final20LiveState`, a `SymbolLiveState`, or a mutable `dict`
                   that will be initialized in-place on first call.
            bar: Mapping/dict/Series/object representing a closed M15 candle with fields:
                 `symbol` (optional if state is single-symbol, defaults to 'BTC-USDT'),
                 `open_ms` (or `open_time` / `timestamp`), `open`, `high`, `low`, `close`, `volume`,
                 and optional `is_closed` (must be True if present), `close_ms`, `provider`.
            only_approved: Optional override. If None, preserves the setting configured on `state` (default True).
            only_warmup_ready: Optional override. If None, preserves the setting configured on `state` (default True).
        """
        if isinstance(state, dict):
            live_state = state.get('_final20_live_state')
            if live_state is None:
                eff_approved = bool(state.get('only_approved', True)) if only_approved is None else bool(only_approved)
                eff_warmup = bool(state.get('only_warmup_ready', True)) if only_warmup_ready is None else bool(only_warmup_ready)
                live_state = self.init_live_state(only_approved=eff_approved, only_warmup_ready=eff_warmup)
                state['_final20_live_state'] = live_state
        elif isinstance(state, Final20LiveState):
            live_state = state
        elif isinstance(state, SymbolLiveState):
            return state.step_bar_dict(bar, only_approved=True if only_approved is None else bool(only_approved), only_warmup_ready=True if only_warmup_ready is None else bool(only_warmup_ready))
        else:
            raise TypeError(f'Unsupported state type for on_bar_close: {type(state)}')
        return live_state.on_bar_close(bar, only_approved=only_approved, only_warmup_ready=only_warmup_ready)

    def init_live_state(self, only_approved: bool=True, only_warmup_ready: bool=True) -> Final20LiveState:
        """Create a fresh incremental state container for `on_bar_close`."""
        return Final20LiveState(spec=self._spec, only_approved=only_approved, only_warmup_ready=only_warmup_ready)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
H1_MS = 3600000

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
H4_MS = 14400000

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
H6_MS = 21600000

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
DAY_MS = 86400000

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncSMA:

    def __init__(self, n: int):
        self.n = n
        self.buf: list[float] = []

    def step(self, x: float) -> float:
        xf = float(x)
        self.buf.append(xf)
        if len(self.buf) > self.n:
            self.buf.pop(0)
        if len(self.buf) < self.n or not all((math.isfinite(v) for v in self.buf)):
            return np.nan
        return sum(self.buf) / self.n

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncStd:

    def __init__(self, n: int):
        self.n = n
        self.buf: list[float] = []

    def step(self, x: float) -> float:
        xf = float(x)
        self.buf.append(xf)
        if len(self.buf) > self.n:
            self.buf.pop(0)
        if len(self.buf) < self.n or not all((math.isfinite(v) for v in self.buf)):
            return np.nan
        mean = sum(self.buf) / self.n
        var = sum(((v - mean) ** 2 for v in self.buf)) / self.n
        return math.sqrt(var)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncRMA:

    def __init__(self, n: int):
        self.n = n
        self.buf: list[float] = []
        self.val: float = np.nan
        self.seeded: bool = False
        self.idx: int = 0

    def step(self, x: float) -> float:
        self.idx += 1
        xf = float(x)
        if not self.seeded:
            self.buf.append(xf)
            if len(self.buf) > self.n:
                self.buf.pop(0)
            if self.idx >= self.n and len(self.buf) == self.n and all((math.isfinite(v) for v in self.buf)):
                self.val = sum(self.buf) / self.n
                self.seeded = True
            return self.val
        if math.isfinite(xf) and math.isfinite(self.val):
            self.val = (self.val * (self.n - 1) + xf) / self.n
        return self.val

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncEMA:

    def __init__(self, n: int):
        self.n = n
        self.alpha = 2.0 / (n + 1.0)
        self.val: float = np.nan
        self.total_count: int = 0

    def step(self, x: float) -> float:
        self.total_count += 1
        xf = float(x)
        if math.isfinite(xf):
            if math.isnan(self.val):
                self.val = xf
            else:
                self.val = (1.0 - self.alpha) * self.val + self.alpha * xf
        if self.total_count < self.n or math.isnan(self.val):
            return np.nan
        return self.val

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncATR:

    def __init__(self, n: int):
        self.rma = _IncRMA(n)
        self.prev_c: float = np.nan

    def step(self, h: float, l: float, c: float) -> float:
        if math.isnan(self.prev_c):
            tr = h - l
        else:
            tr = max(h - l, abs(h - self.prev_c), abs(l - self.prev_c))
        self.prev_c = c
        return self.rma.step(tr)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncRSI:

    def __init__(self, n: int=14):
        self.au = _IncRMA(n)
        self.ad = _IncRMA(n)
        self.prev_c: float = np.nan

    def step(self, c: float) -> float:
        if math.isnan(self.prev_c):
            up = np.nan
            dn = np.nan
        else:
            d = c - self.prev_c
            up = d if d > 0.0 else 0.0
            dn = -d if d < 0.0 else 0.0
        self.prev_c = c
        u = self.au.step(up)
        d_val = self.ad.step(dn)
        if math.isnan(u) or math.isnan(d_val):
            return np.nan
        if u == 0.0:
            return 0.0
        if d_val == 0.0:
            return 100.0
        rs = u / d_val
        return 100.0 - 100.0 / (1.0 + rs)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncMFI:

    def __init__(self, n: int=14):
        self.n = n
        self.prev_tp: float = np.nan
        self.pos_buf: list[float] = []
        self.neg_buf: list[float] = []

    def step(self, h: float, l: float, c: float, v: float) -> float:
        tp = (h + l + c) / 3.0
        mf = tp * v
        if math.isnan(self.prev_tp):
            pos = 0.0
            neg = 0.0
        else:
            d = tp - self.prev_tp
            pos = mf if d > 0.0 else 0.0
            neg = mf if d < 0.0 else 0.0
        self.prev_tp = tp
        self.pos_buf.append(pos)
        self.neg_buf.append(neg)
        if len(self.pos_buf) > self.n:
            self.pos_buf.pop(0)
            self.neg_buf.pop(0)
        if len(self.pos_buf) < self.n:
            return np.nan
        ps = sum(self.pos_buf)
        ns = sum(self.neg_buf)
        if ns == 0.0:
            return 100.0
        ratio = ps / ns
        return 100.0 - 100.0 / (1.0 + ratio)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncCCI:

    def __init__(self, n: int=9):
        self.n = n
        self.c_buf: list[float] = []

    def step(self, c: float) -> float:
        self.c_buf.append(float(c))
        if len(self.c_buf) > self.n:
            self.c_buf.pop(0)
        if len(self.c_buf) < self.n:
            return np.nan
        ma = sum(self.c_buf) / self.n
        md = sum((abs(v - ma) for v in self.c_buf)) / self.n
        if md == 0.0:
            return np.nan
        return (c - ma) / (0.015 * md)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncStoch:

    def __init__(self, n: int, d_len: int=3):
        self.n = n
        self.h_buf: list[float] = []
        self.l_buf: list[float] = []
        self.d_sma = _IncSMA(d_len)

    def step(self, h: float, l: float, c: float) -> tuple[float, float]:
        self.h_buf.append(h)
        self.l_buf.append(l)
        if len(self.h_buf) > self.n:
            self.h_buf.pop(0)
            self.l_buf.pop(0)
        if len(self.h_buf) < self.n:
            k = np.nan
        else:
            lo = min(self.l_buf)
            hi = max(self.h_buf)
            k = 100.0 * (c - lo) / (hi - lo) if hi != lo else np.nan
        d = self.d_sma.step(k)
        return (k, d)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncDMI:

    def __init__(self, di_len: int=14, adx_len: int=14):
        self.tr_rma = _IncRMA(di_len)
        self.plus_rma = _IncRMA(di_len)
        self.minus_rma = _IncRMA(di_len)
        self.adx_rma = _IncRMA(adx_len)
        self.prev_h: float = np.nan
        self.prev_l: float = np.nan
        self.prev_c: float = np.nan

    def step(self, h: float, l: float, c: float) -> tuple[float, float, float]:
        if math.isnan(self.prev_c):
            tr = h - l
            plus_dm = 0.0
            minus_dm = 0.0
        else:
            tr = max(h - l, abs(h - self.prev_c), abs(l - self.prev_c))
            up = h - self.prev_h
            down = -(l - self.prev_l)
            plus_dm = up if up > down and up > 0.0 else 0.0
            minus_dm = down if down > up and down > 0.0 else 0.0
        self.prev_h = h
        self.prev_l = l
        self.prev_c = c
        tr_v = self.tr_rma.step(tr)
        p_v = self.plus_rma.step(plus_dm)
        m_v = self.minus_rma.step(minus_dm)
        if math.isnan(tr_v) or tr_v == 0.0 or math.isnan(p_v) or math.isnan(m_v):
            plus = np.nan
            minus = np.nan
            dx = np.nan
        else:
            plus = 100.0 * p_v / tr_v
            minus = 100.0 * m_v / tr_v
            denom = plus + minus
            dx = 100.0 * abs(plus - minus) / denom if denom != 0.0 else np.nan
        adx = self.adx_rma.step(dx)
        return (plus, minus, adx)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncPSAR:

    def __init__(self, start: float=0.02, inc: float=0.02, max_af: float=0.2):
        self.start = start
        self.inc = inc
        self.max_af = max_af
        self.idx = 0
        self.h0 = np.nan
        self.l0 = np.nan
        self.c0 = np.nan
        self.h_prev1 = np.nan
        self.h_prev2 = np.nan
        self.l_prev1 = np.nan
        self.l_prev2 = np.nan
        self.bull = True
        self.af = start
        self.ep = np.nan
        self.sar = np.nan

    def step(self, h: float, l: float, c: float) -> float:
        i = self.idx
        self.idx += 1
        if i == 0:
            self.h0, self.l0, self.c0 = (h, l, c)
            self.h_prev1, self.l_prev1 = (h, l)
            return np.nan
        if i == 1:
            self.bull = c >= self.c0
            self.af = self.start
            self.ep = self.h0 if self.bull else self.l0
            self.sar = self.l0 if self.bull else self.h0
        sar = self.sar + self.af * (self.ep - self.sar)
        if self.bull:
            sar = min(sar, self.l_prev1, self.l_prev2) if i >= 2 else min(sar, self.l_prev1)
            if l < sar:
                self.bull = False
                sar = self.ep
                self.ep = l
                self.af = self.start
            elif h > self.ep:
                self.ep = h
                self.af = min(self.max_af, self.af + self.inc)
        else:
            sar = max(sar, self.h_prev1, self.h_prev2) if i >= 2 else max(sar, self.h_prev1)
            if h > sar:
                self.bull = True
                sar = self.ep
                self.ep = h
                self.af = self.start
            elif l < self.ep:
                self.ep = l
                self.af = min(self.max_af, self.af + self.inc)
        self.sar = sar
        self.h_prev2 = self.h_prev1
        self.l_prev2 = self.l_prev1
        self.h_prev1 = h
        self.l_prev1 = l
        return sar

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncCustomSupertrend:

    def __init__(self, atr_n: int, factor: float):
        self.atr = _IncATR(atr_n)
        self.factor = factor
        self.prev_trend: float = np.nan
        self.prev_c: float = np.nan
        self.idx = 0

    def step(self, h: float, l: float, c: float) -> int:
        a = self.atr.step(h, l, c)
        mid = (h + l) / 2.0
        up = mid + self.factor * a if math.isfinite(a) else np.nan
        lo = mid - self.factor * a if math.isfinite(a) else np.nan
        if self.idx == 0 or not math.isfinite(self.prev_trend):
            trend = mid
        elif self.prev_c > self.prev_trend:
            trend = max(lo, self.prev_trend) if math.isfinite(lo) else self.prev_trend
        else:
            trend = min(up, self.prev_trend) if math.isfinite(up) else self.prev_trend
        self.prev_trend = trend
        self.prev_c = c
        self.idx += 1
        return 1 if c > trend else -1

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncCombo15Supertrend:

    def __init__(self):
        self.atr = _IncATR(14)
        self.c_buf: list[float] = []
        self.prev_out: int = 1
        self.idx = 0

    def step(self, h: float, l: float, c: float) -> int:
        a = self.atr.step(h, l, c)
        self.c_buf.append(c)
        if len(self.c_buf) > 8:
            self.c_buf.pop(0)
        base = sum(self.c_buf) / 8.0 if len(self.c_buf) == 8 else np.nan
        u = base + 2.0 * a if math.isfinite(base) and math.isfinite(a) else np.nan
        lo = base - 2.0 * a if math.isfinite(base) and math.isfinite(a) else np.nan
        prev = self.prev_out if self.idx > 0 else 1
        if math.isfinite(lo) and c > lo:
            out = 1
        elif math.isfinite(u) and c < u:
            out = -1
        else:
            out = prev
        self.prev_out = out
        self.idx += 1
        return out

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncNadaraya:

    def __init__(self, length: int=24, smooth: float=3.0):
        self.length = length
        self.weights = [math.exp(-(i / length * smooth) ** 2) for i in range(length + 1)]
        self.denom = sum(self.weights)
        self.c_buf: list[float] = []

    def step(self, c: float) -> float:
        self.c_buf.append(c)
        if len(self.c_buf) > self.length + 1:
            self.c_buf.pop(0)
        if len(self.c_buf) < self.length + 1:
            return np.nan
        num = sum((self.c_buf[-1 - i] * self.weights[i] for i in range(self.length + 1)))
        return num / self.denom

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncATRTrailingStop:

    def __init__(self):
        self.prev_ats: float = np.nan
        self.prev_c: float = np.nan
        self.idx = 0

    def step(self, c: float, atr14: float) -> tuple[float, bool, bool]:
        ts = 1.5 * atr14
        nz = 0.0 if not math.isfinite(self.prev_ats) else self.prev_ats
        if self.idx > 0 and c > nz and (self.prev_c > nz):
            ats = max(nz, c - ts)
        elif self.idx > 0 and c < nz and (self.prev_c < nz):
            ats = min(nz, c + ts)
        else:
            ats = c - ts if c > nz else c + ts
        above = self.idx > 0 and math.isfinite(ats) and math.isfinite(self.prev_ats) and (c > ats) and (self.prev_c <= self.prev_ats)
        below = self.idx > 0 and math.isfinite(ats) and math.isfinite(self.prev_ats) and (ats > c) and (self.prev_ats <= self.prev_c)
        self.prev_ats = ats
        self.prev_c = c
        self.idx += 1
        return (ats, above, below)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncSMC:

    def __init__(self, swing_len: int=50, confluence: bool=False):
        self.swing_len = swing_len
        self.confluence = confluence
        self.h_buf: list[float] = []
        self.l_buf: list[float] = []
        self.prev_c: float = np.nan
        self.idx = 0
        self.swing_leg = 0
        self.internal_leg = 0
        self.swing_hi = np.nan
        self.swing_lo = np.nan
        self.internal_hi = np.nan
        self.internal_lo = np.nan
        self.swing_hi_crossed = False
        self.swing_lo_crossed = False
        self.internal_hi_crossed = False
        self.internal_lo_crossed = False
        self.swing_bias = 0
        self.internal_bias = 0

    def step(self, o: float, h: float, l: float, c: float) -> int:
        i = self.idx
        self.idx += 1
        L = self.swing_len
        self.h_buf.append(h)
        self.l_buf.append(l)
        if len(self.h_buf) > L + 1:
            self.h_buf.pop(0)
            self.l_buf.pop(0)
        swing_new_high = i >= L and self.h_buf[0] > max(self.h_buf[1:])
        swing_new_low = i >= L and self.l_buf[0] < min(self.l_buf[1:])
        old = self.swing_leg
        if swing_new_high:
            self.swing_leg = 0
        elif swing_new_low:
            self.swing_leg = 1
        swing_change = self.swing_leg - old
        ilen = 5
        int_new_high = i >= ilen and self.h_buf[-ilen - 1] > max(self.h_buf[-ilen:])
        int_new_low = i >= ilen and self.l_buf[-ilen - 1] < min(self.l_buf[-ilen:])
        oldi = self.internal_leg
        if int_new_high:
            self.internal_leg = 0
        elif int_new_low:
            self.internal_leg = 1
        int_change = self.internal_leg - oldi
        prev_swing_hi = self.swing_hi
        prev_swing_lo = self.swing_lo
        prev_internal_hi = self.internal_hi
        prev_internal_lo = self.internal_lo
        if swing_change == 1:
            self.swing_lo = self.l_buf[0]
            self.swing_lo_crossed = False
        elif swing_change == -1:
            self.swing_hi = self.h_buf[0]
            self.swing_hi_crossed = False
        if int_change == 1:
            self.internal_lo = self.l_buf[-ilen - 1]
            self.internal_lo_crossed = False
        elif int_change == -1:
            self.internal_hi = self.h_buf[-ilen - 1]
            self.internal_hi_crossed = False
        bullbar = True
        bearbar = True
        if self.confluence:
            upper = h - max(c, o)
            rhs = min(c, o - l)
            bullbar = upper > rhs
            bearbar = upper < rhs

        def _xover(level: float, prev_level: float) -> bool:
            return i > 0 and math.isfinite(level) and math.isfinite(prev_level) and (c > level) and (self.prev_c <= prev_level)

        def _xunder(level: float, prev_level: float) -> bool:
            return i > 0 and math.isfinite(level) and math.isfinite(prev_level) and (c < level) and (self.prev_c >= prev_level)
        internal_bull_extra = math.isfinite(self.internal_hi) and math.isfinite(self.swing_hi) and (self.internal_hi != self.swing_hi) and bullbar
        if not self.internal_hi_crossed and internal_bull_extra and _xover(self.internal_hi, prev_internal_hi):
            self.internal_hi_crossed = True
            self.internal_bias = 1
        internal_bear_extra = math.isfinite(self.internal_lo) and math.isfinite(self.swing_lo) and (self.internal_lo != self.swing_lo) and bearbar
        if not self.internal_lo_crossed and internal_bear_extra and _xunder(self.internal_lo, prev_internal_lo):
            self.internal_lo_crossed = True
            self.internal_bias = -1
        if not self.swing_hi_crossed and _xover(self.swing_hi, prev_swing_hi):
            self.swing_hi_crossed = True
            self.swing_bias = 1
        if not self.swing_lo_crossed and _xunder(self.swing_lo, prev_swing_lo):
            self.swing_lo_crossed = True
            self.swing_bias = -1
        self.prev_c = c
        if self.internal_bias == 1 and self.swing_bias == 1:
            return 1
        if self.internal_bias == -1 and self.swing_bias == -1:
            return -1
        return 0

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncOB:

    def __init__(self, pivot_len: int=5, search_bars: int=12, max_age: int=80, danger_atr: float=0.5):
        self.pivot_len = pivot_len
        self.search_bars = search_bars
        self.max_age = max_age
        self.danger_atr = danger_atr
        self.atr = _IncATR(14)
        self.o_buf: list[float] = []
        self.h_buf: list[float] = []
        self.l_buf: list[float] = []
        self.c_buf: list[float] = []
        self.idx = 0
        self.sh = np.nan
        self.sl = np.nan
        self.sh_broken = False
        self.sl_broken = False
        self.blo = np.nan
        self.bhi = np.nan
        self.bro = -1
        self.elo = np.nan
        self.ehi = np.nan
        self.ero = -1

    def step(self, o: float, h: float, l: float, c: float) -> tuple[bool, bool]:
        i = self.idx
        self.idx += 1
        a = self.atr.step(h, l, c)
        n = self.pivot_len
        keep = max(2 * n + 1, self.search_bars + 1)
        self.o_buf.append(o)
        self.h_buf.append(h)
        self.l_buf.append(l)
        self.c_buf.append(c)
        if len(self.h_buf) > keep:
            self.o_buf.pop(0)
            self.h_buf.pop(0)
            self.l_buf.pop(0)
            self.c_buf.pop(0)
        ph_i = np.nan
        pl_i = np.nan
        if i >= 2 * n:
            mid_idx = len(self.h_buf) - 1 - n
            hj = self.h_buf[mid_idx]
            lj = self.l_buf[mid_idx]
            neighbors_h = self.h_buf[mid_idx - n:mid_idx] + self.h_buf[mid_idx + 1:mid_idx + n + 1]
            neighbors_l = self.l_buf[mid_idx - n:mid_idx] + self.l_buf[mid_idx + 1:mid_idx + n + 1]
            if hj > max(neighbors_h):
                ph_i = hj
            if lj < min(neighbors_l):
                pl_i = lj
        if math.isfinite(ph_i):
            self.sh = ph_i
            self.sh_broken = False
        if math.isfinite(pl_i):
            self.sl = pl_i
            self.sl_broken = False
        bull_bos = math.isfinite(self.sh) and (not self.sh_broken) and (c > self.sh)
        bear_bos = math.isfinite(self.sl) and (not self.sl_broken) and (c < self.sl)
        if bull_bos:
            self.sh_broken = True
            for j in range(1, min(self.search_bars, i) + 1):
                k_buf = len(self.c_buf) - 1 - j
                if self.c_buf[k_buf] < self.o_buf[k_buf]:
                    self.blo = self.l_buf[k_buf]
                    self.bhi = self.h_buf[k_buf]
                    self.bro = i - j
                    break
        if bear_bos:
            self.sl_broken = True
            for j in range(1, min(self.search_bars, i) + 1):
                k_buf = len(self.c_buf) - 1 - j
                if self.c_buf[k_buf] > self.o_buf[k_buf]:
                    self.elo = self.l_buf[k_buf]
                    self.ehi = self.h_buf[k_buf]
                    self.ero = i - j
                    break
        if math.isfinite(self.blo) and (i - self.bro > self.max_age or c < self.blo):
            self.blo = np.nan
            self.bhi = np.nan
            self.bro = -1
        if math.isfinite(self.ehi) and (i - self.ero > self.max_age or c > self.ehi):
            self.elo = np.nan
            self.ehi = np.nan
            self.ero = -1
        danger = a * self.danger_atr if math.isfinite(a) else 0.0
        buy_blocked = bool(math.isfinite(self.elo) and c <= self.ehi and (self.elo - c <= danger))
        sell_blocked = bool(math.isfinite(self.bhi) and c >= self.blo and (c - self.bhi <= danger))
        return (buy_blocked, sell_blocked)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncDeMarker:

    def __init__(self, n: int=60):
        self.prev_h = np.nan
        self.prev_l = np.nan
        self.max_sma = _IncSMA(n)
        self.min_sma = _IncSMA(n)

    def step(self, h: float, l: float) -> float:
        if math.isnan(self.prev_h):
            demax = np.nan
            demin = np.nan
        else:
            demax = max(h - self.prev_h, 0.0)
            demin = max(self.prev_l - l, 0.0)
        self.prev_h = h
        self.prev_l = l
        a = self.max_sma.step(demax)
        b = self.min_sma.step(demin)
        if math.isnan(a) or math.isnan(b) or a + b == 0.0:
            return np.nan
        return a / (a + b)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncAroon:

    def __init__(self, period: int=40):
        self.period = period
        self.h_buf: list[float] = []
        self.l_buf: list[float] = []

    def step(self, h: float, l: float) -> tuple[float, float]:
        self.h_buf.append(h)
        self.l_buf.append(l)
        if len(self.h_buf) > self.period:
            self.h_buf.pop(0)
            self.l_buf.pop(0)
        if len(self.h_buf) < self.period:
            return (np.nan, np.nan)
        imax = 0
        imin = 0
        mx = self.h_buf[0]
        mn = self.l_buf[0]
        for j in range(1, self.period):
            if self.h_buf[j] >= mx:
                mx = self.h_buf[j]
                imax = j
            if self.l_buf[j] <= mn:
                mn = self.l_buf[j]
                imin = j
        since_h = self.period - 1 - imax
        since_l = self.period - 1 - imin
        up = 100.0 * (self.period - since_h) / self.period
        down = 100.0 * (self.period - since_l) / self.period
        return (up, down)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncCMO:

    def __init__(self, length: int=40):
        self.length = length
        self.prev_c = np.nan
        self.up_buf: list[float] = []
        self.dn_buf: list[float] = []

    def step(self, c: float) -> float:
        if math.isnan(self.prev_c):
            up = np.nan
            dn = np.nan
        else:
            d = c - self.prev_c
            up = d if d > 0.0 else 0.0
            dn = -d if d < 0.0 else 0.0
        self.prev_c = c
        self.up_buf.append(up)
        self.dn_buf.append(dn)
        if len(self.up_buf) > self.length:
            self.up_buf.pop(0)
            self.dn_buf.pop(0)
        if len(self.up_buf) < self.length or not all((math.isfinite(v) for v in self.up_buf)):
            return np.nan
        u = sum(self.up_buf)
        d_val = sum(self.dn_buf)
        if u + d_val == 0.0:
            return np.nan
        return 100.0 * (u - d_val) / (u + d_val)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncIchimoku:

    def __init__(self, conv: int=12, base: int=30, spanb: int=60):
        self.conv = conv
        self.base = base
        self.spanb = spanb
        self.h_buf: list[float] = []
        self.l_buf: list[float] = []
        self.sa_hist: list[float] = []
        self.sb_hist: list[float] = []

    def step(self, h: float, l: float) -> tuple[float, float, float, float]:
        self.h_buf.append(h)
        self.l_buf.append(l)
        max_len = max(self.conv, self.base, self.spanb)
        if len(self.h_buf) > max_len:
            self.h_buf.pop(0)
            self.l_buf.pop(0)
        tenkan = (max(self.h_buf[-self.conv:]) + min(self.l_buf[-self.conv:])) / 2.0 if len(self.h_buf) >= self.conv else np.nan
        kijun = (max(self.h_buf[-self.base:]) + min(self.l_buf[-self.base:])) / 2.0 if len(self.h_buf) >= self.base else np.nan
        sa_raw = (tenkan + kijun) / 2.0 if math.isfinite(tenkan) and math.isfinite(kijun) else np.nan
        sb_raw = (max(self.h_buf[-self.spanb:]) + min(self.l_buf[-self.spanb:])) / 2.0 if len(self.h_buf) >= self.spanb else np.nan
        self.sa_hist.append(sa_raw)
        self.sb_hist.append(sb_raw)
        if len(self.sa_hist) > self.base + 1:
            self.sa_hist.pop(0)
            self.sb_hist.pop(0)
        span_a = self.sa_hist[0] if len(self.sa_hist) == self.base + 1 else np.nan
        span_b = self.sb_hist[0] if len(self.sb_hist) == self.base + 1 else np.nan
        return (tenkan, kijun, span_a, span_b)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncKAMA:

    def __init__(self, er_len: int=40, fast_len: int=2, slow_len: int=40):
        self.er_len = er_len
        self.fast = 2.0 / (fast_len + 1.0)
        self.slow = 2.0 / (slow_len + 1.0)
        self.c_buf: list[float] = []
        self.kama = np.nan
        self.idx = 0

    def step(self, c: float) -> tuple[float, float, float]:
        i = self.idx
        self.idx += 1
        self.c_buf.append(c)
        if len(self.c_buf) > self.er_len + 1:
            self.c_buf.pop(0)
        if i < self.er_len:
            return (np.nan, np.nan, np.nan)
        change = abs(self.c_buf[-1] - self.c_buf[0])
        vol = sum((abs(self.c_buf[j] - self.c_buf[j - 1]) for j in range(1, len(self.c_buf))))
        er = change / vol if vol > 1e-12 else 0.0
        prev_kama = self.kama
        if i == self.er_len:
            self.kama = c
            slope = np.nan
        else:
            sc = (er * (self.fast - self.slow) + self.slow) ** 2
            self.kama = self.kama + sc * (c - self.kama)
            slope = self.kama - prev_kama
        return (self.kama, er, slope)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _IncVortex:

    def __init__(self, length: int=14):
        self.length = length
        self.prev_h = np.nan
        self.prev_l = np.nan
        self.prev_c = np.nan
        self.tr_buf: list[float] = []
        self.vmp_buf: list[float] = []
        self.vmm_buf: list[float] = []

    def step(self, h: float, l: float, c: float) -> tuple[float, float]:
        if math.isnan(self.prev_c):
            tr = h - l
            vmp = np.nan
            vmm = np.nan
        else:
            tr = max(h - l, abs(h - self.prev_c), abs(l - self.prev_c))
            vmp = abs(h - self.prev_l)
            vmm = abs(l - self.prev_h)
        self.prev_h = h
        self.prev_l = l
        self.prev_c = c
        self.tr_buf.append(tr)
        self.vmp_buf.append(vmp)
        self.vmm_buf.append(vmm)
        if len(self.tr_buf) > self.length:
            self.tr_buf.pop(0)
            self.vmp_buf.pop(0)
            self.vmm_buf.pop(0)
        if len(self.tr_buf) < self.length or not all((math.isfinite(v) for v in self.vmp_buf)):
            return (np.nan, np.nan)
        tr_sum = sum(self.tr_buf)
        if tr_sum == 0.0:
            return (np.nan, np.nan)
        return (sum(self.vmp_buf) / tr_sum, sum(self.vmm_buf) / tr_sum)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class _PeriodAggregator:
    """Resamples 15m bars into higher timeframe periods (1h, 4h, 6h) with label='left', closed='left'."""

    def __init__(self, period_ms: int):
        self.period_ms = period_ms
        self.cur_period_open_ms: int | None = None
        self.o = np.nan
        self.h = np.nan
        self.l = np.nan
        self.c = np.nan
        self.v = 0.0

    def update(self, open_ms: int, o: float, h: float, l: float, c: float, v: float) -> tuple[bool, int, float, float, float, float, float]:
        p_open = open_ms // self.period_ms * self.period_ms
        if self.cur_period_open_ms != p_open:
            self.cur_period_open_ms = p_open
            self.o = o
            self.h = h
            self.l = l
            self.c = c
            self.v = v
        else:
            if h > self.h:
                self.h = h
            if l < self.l:
                self.l = l
            self.c = c
            self.v += v
        is_period_close = (open_ms + M15_MS) % self.period_ms == 0
        return (is_period_close, p_open, self.o, self.h, self.l, self.c, self.v)

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class SymbolLiveState:
    """Per-symbol O(1) incremental indicator and signal state."""

    def __init__(self, symbol: str, combos: list[ComboSpec], warmup_bars: int):
        self.symbol = symbol
        self.combos = sorted(combos, key=lambda c: c.combo_id)
        self.warmup_bars = warmup_bars
        self.bar_pos = 0
        self.last_open_ms: int | None = None
        self.agg_1h = _PeriodAggregator(H1_MS)
        self.agg_4h = _PeriodAggregator(H4_MS)
        self.agg_6h = _PeriodAggregator(H6_MS)
        self.ema4h_50 = _IncEMA(50)
        self.ema4h_100 = _IncEMA(100)
        self.ema4h_150 = _IncEMA(150)
        self.ema4h_200 = _IncEMA(200)
        self.ema6h_50 = _IncEMA(50)
        self.confirmed_ema4h_50 = np.nan
        self.prev_confirmed_ema4h_50_1h = np.nan
        self.confirmed_ema4h_100 = np.nan
        self.confirmed_ema4h_150 = np.nan
        self.confirmed_ema4h_200 = np.nan
        self.confirmed_ema6h_50 = np.nan
        self.m15_atr14 = _IncATR(14)
        self.m15_rsi14 = _IncRSI(14)
        self.m15_mfi14 = _IncMFI(14)
        self.m15_dmi14 = _IncDMI(14, 14)
        self.m15_vol_sma20 = _IncSMA(20)
        self.m15_ema21 = _IncEMA(21)
        self.m15_ema55 = _IncEMA(55)
        self.m15_ema200 = _IncEMA(200)
        self.m15_st15 = _IncCombo15Supertrend()
        self.m15_nw = _IncNadaraya(24, 3.0)
        self.m15_std24 = _IncStd(24)
        self.m15_hl2_sma5 = _IncSMA(5)
        self.m15_hl2_sma34 = _IncSMA(34)
        self.m15_ema20 = _IncEMA(20)
        self.m15_ema50 = _IncEMA(50)
        self.m15_c_sma20 = _IncSMA(20)
        self.m15_c_std20 = _IncStd(20)
        self.m15_smc = _IncSMC(50, False)
        self.m15_ob = _IncOB(5, 12, 80, 0.5)
        self.m15_prev_adx = np.nan
        self.m15_prev_vol = np.nan
        self.h1_atr21 = _IncATR(21)
        self.h1_atr14 = _IncATR(14)
        self.h1_atr20 = _IncATR(20)
        self.h1_rsi14 = _IncRSI(14)
        self.h1_rsi_sma14 = _IncSMA(14)
        self.h1_mfi14 = _IncMFI(14)
        self.h1_dmi14 = _IncDMI(14, 14)
        self.h1_vol_sma20 = _IncSMA(20)
        self.h1_stoch11 = _IncStoch(11, 3)
        self.h1_stoch14 = _IncStoch(14, 3)
        self.h1_cci9 = _IncCCI(9)
        self.h1_ema8 = _IncEMA(8)
        self.h1_ema21 = _IncEMA(21)
        self.h1_macd8_sig9 = _IncEMA(9)
        self.h1_ema10 = _IncEMA(10)
        self.h1_ema25 = _IncEMA(25)
        self.h1_ema42 = _IncEMA(42)
        self.h1_c_sma20 = _IncSMA(20)
        self.h1_c_std20 = _IncStd(20)
        self.h1_ats = _IncATRTrailingStop()
        self.h1_st_custom = _IncCustomSupertrend(8, 4.0)
        self.h1_psar = _IncPSAR(0.05, 0.2, 0.3)
        self.h1_ema12 = _IncEMA(12)
        self.h1_ema26 = _IncEMA(26)
        self.h1_macd12_sig9 = _IncEMA(9)
        self.h1_ema9 = _IncEMA(9)
        self.h1_c_sma8 = _IncSMA(8)
        self.h1_ema5 = _IncEMA(5)
        self.h1_ema34 = _IncEMA(34)
        self.h1_ema20 = _IncEMA(20)
        self.h1_ema50 = _IncEMA(50)
        self.h1_smc = _IncSMC(50, False)
        self.h1_ob = _IncOB(5, 12, 80, 0.5)
        self.h1_prev_adx = np.nan
        self.h1_prev_cci9 = np.nan
        self.h1_prev_c = np.nan
        self.h1_prev_o = np.nan
        self.h1_obv = 0.0
        self.h1_prev_obv = np.nan
        self.h1_obv_seeded = False
        self.h1_prev_bbu = np.nan
        self.h1_prev_bbl = np.nan
        self.h1_prev_atr20 = np.nan
        self.h1_prev_mfi = np.nan
        self.h1_prev_k14 = np.nan
        self.h1_prev_d14 = np.nan
        self.h1_c_win20: list[float] = []
        self.h1_cur_day: int | None = None
        self.h1_vwap_pv = 0.0
        self.h1_vwap_vol = 0.0
        self.prev_cond_L: dict[str, bool] = {c.combo_key: False for c in self.combos}
        self.prev_cond_S: dict[str, bool] = {c.combo_key: False for c in self.combos}
        self.new6_states: dict[str, dict[str, Any]] = {}
        for c in self.combos:
            if c.is_new6:
                cid = c.combo_id
                ent = c.entry
                if cid == 101:
                    self.new6_states[c.combo_key] = {'ind': _IncDeMarker(int(ent.get('period', 60)))}
                elif cid == 102:
                    self.new6_states[c.combo_key] = {'ind': _IncAroon(int(ent.get('period', 40))), 'prev_up': np.nan, 'prev_dn': np.nan}
                elif cid == 103:
                    self.new6_states[c.combo_key] = {'ind': _IncCMO(int(ent.get('period', 40))), 'prev_z': np.nan}
                elif cid == 104:
                    self.new6_states[c.combo_key] = {'ind': _IncIchimoku(int(ent.get('conv', 12)), int(ent.get('base', 30)), int(ent.get('spanb', 60))), 'prev_t': np.nan, 'prev_k': np.nan}
                elif cid == 105:
                    self.new6_states[c.combo_key] = {'ind': _IncKAMA(int(ent.get('er_len', 40)), int(ent.get('fast_len', 2)), int(ent.get('slow_len', 40)))}
                elif cid == 106:
                    self.new6_states[c.combo_key] = {'ind': _IncVortex(int(ent.get('period', 14))), 'prev_vip': np.nan, 'prev_vim': np.nan}

    def step_bar_dict(self, bar: Any, only_approved: bool=True, only_warmup_ready: bool=True) -> list[SignalEvent]:
        if isinstance(bar, dict):
            get_fn = bar.get
        elif hasattr(bar, 'get'):
            get_fn = bar.get
        else:

            def get_fn(k: str, default: Any=None) -> Any:
                return getattr(bar, k, default)
        is_closed = get_fn('is_closed', True)
        if not is_closed:
            raise ValueError('on_bar_close rejected unclosed candle (is_closed=False)')
        raw_ts = get_fn('open_ms')
        if raw_ts is None:
            raw_ts = get_fn('open_time')
        if raw_ts is None:
            raw_ts = get_fn('timestamp')
        if raw_ts is None and isinstance(bar, pd.Series) and isinstance(bar.name, pd.Timestamp):
            raw_ts = int(bar.name.value // 1000000)
        if isinstance(raw_ts, pd.Timestamp):
            open_ms = int(raw_ts.value // 1000000)
        else:
            open_ms = int(raw_ts)
        if open_ms % M15_MS != 0:
            raise ValueError(f'M15 bar open_ms is not aligned to 15-minute boundary: {open_ms}')
        close_ms_arg = get_fn('close_ms')
        if close_ms_arg is not None and int(close_ms_arg) < open_ms + M15_MS - 1:
            raise ValueError(f'on_bar_close rejected incomplete M15 candle: close_ms={close_ms_arg} < {open_ms + M15_MS - 1}')
        o = float(get_fn('open'))
        h = float(get_fn('high'))
        l = float(get_fn('low'))
        c = float(get_fn('close'))
        v = float(get_fn('volume'))
        provider = str(get_fn('provider', 'UNKNOWN') or 'UNKNOWN')
        return self.step_bar(open_ms=open_ms, o=o, h=h, l=l, c=c, v=v, provider=provider, only_approved=only_approved, only_warmup_ready=only_warmup_ready)

    def step_bar(self, open_ms: int, o: float, h: float, l: float, c: float, v: float, provider: str='UNKNOWN', only_approved: bool=True, only_warmup_ready: bool=True) -> list[SignalEvent]:
        if open_ms % M15_MS != 0:
            raise ValueError(f'M15 bar open_ms is not aligned to 15-minute boundary: {open_ms}')
        if self.last_open_ms is not None and open_ms - self.last_open_ms != M15_MS:
            raise ValueError(f'M15 bar gap or out-of-order: prev={self.last_open_ms}, cur={open_ms}')
        self.last_open_ms = open_ms
        pos = self.bar_pos
        self.bar_pos += 1
        cur_ema4h_50 = self.confirmed_ema4h_50
        cur_ema4h_100 = self.confirmed_ema4h_100
        cur_ema4h_150 = self.confirmed_ema4h_150
        cur_ema4h_200 = self.confirmed_ema4h_200
        cur_ema6h_50 = self.confirmed_ema6h_50
        a15 = self.m15_atr14.step(h, l, c)
        r15 = self.m15_rsi14.step(c)
        _ = self.m15_mfi14.step(h, l, c, v)
        _, _, adx15 = self.m15_dmi14.step(h, l, c)
        vol_sma15 = self.m15_vol_sma20.step(v)
        body15 = abs(c - o)
        rng15 = h - l
        body_ratio15 = body15 / rng15 if rng15 != 0.0 else np.nan
        valid15 = math.isfinite(body_ratio15) and body_ratio15 > 0.75
        ef15 = self.m15_ema21.step(c)
        es15 = self.m15_ema55.step(c)
        ema200_15 = self.m15_ema200.step(c)
        st15 = self.m15_st15.step(h, l, c)
        nw15 = self.m15_nw.step(c)
        std24_15 = self.m15_std24.step(c)
        hl2 = (h + l) / 2.0
        ao15 = self.m15_hl2_sma5.step(hl2) - self.m15_hl2_sma34.step(hl2)
        sq15 = self.m15_ema20.step(c) - self.m15_ema50.step(c)
        mean20_15 = self.m15_c_sma20.step(c)
        std20_15 = self.m15_c_std20.step(c)
        z15 = (c - mean20_15) / std20_15 if math.isfinite(std20_15) and std20_15 != 0.0 else np.nan
        smc15 = self.m15_smc.step(o, h, l, c)
        ob15_buy, ob15_sell = self.m15_ob.step(o, h, l, c)
        adxr15 = math.isfinite(adx15) and math.isfinite(self.m15_prev_adx) and (adx15 > self.m15_prev_adx)
        vol_surge15 = math.isfinite(self.m15_prev_vol) and v > self.m15_prev_vol * 1.8
        tu100_15 = math.isfinite(cur_ema4h_100) and c > cur_ema4h_100
        td100_15 = math.isfinite(cur_ema4h_100) and c < cur_ema4h_100
        self.m15_prev_adx = adx15
        self.m15_prev_vol = v
        raw_candidates: list[tuple[ComboSpec, str, int, int, bool, bool]] = []
        for combo in self.combos:
            if combo.native_timeframe != '15m':
                continue
            cid = combo.combo_id
            ent = combo.entry
            cond_l = False
            cond_s = False
            if cid == 7:
                bm = float(ent.get('body_atr', 1.6))
                vm = float(ent.get('volume_mult', 2.0))
                strong_b = c > o and math.isfinite(a15) and (c - o > a15 * bm)
                strong_s = c < o and math.isfinite(a15) and (o - c > a15 * bm)
                volsp = math.isfinite(vol_sma15) and v > vol_sma15 * vm
                cond_l = bool(vol_surge15 and math.isfinite(ef15) and math.isfinite(es15) and (ef15 > es15) and tu100_15 and adxr15 and (adx15 > 25.0) and strong_b and volsp and valid15 and math.isfinite(r15) and (r15 > 60.0) and (r15 < 85.0))
                cond_s = bool(vol_surge15 and math.isfinite(ef15) and math.isfinite(es15) and (ef15 < es15) and td100_15 and adxr15 and (adx15 > 25.0) and strong_s and volsp and valid15 and math.isfinite(r15) and (r15 < 40.0) and (r15 > 15.0))
            elif cid == 9:
                r_long = float(ent.get('rsi_long_max', 45.0))
                r_short = float(ent.get('rsi_short_min', 100.0 - r_long))
                fix_st = bool(ent.get('fix_short_supertrend', True))
                nr = std24_15 * 2.3 if math.isfinite(std24_15) else np.nan
                upper = nw15 + nr if math.isfinite(nw15) and math.isfinite(nr) else np.nan
                lower = nw15 - nr if math.isfinite(nw15) and math.isfinite(nr) else np.nan
                br_ok = math.isfinite(body_ratio15) and body_ratio15 > 0.4
                cond_l = bool(math.isfinite(ema200_15) and c > ema200_15 and math.isfinite(r15) and (r15 < r_long) and math.isfinite(lower) and (c < lower) and (st15 == 1) and br_ok)
                st_cond = st15 == -1 if fix_st else st15 == 1
                cond_s = bool(math.isfinite(ema200_15) and c < ema200_15 and math.isfinite(r15) and (r15 > r_short) and math.isfinite(upper) and (c > upper) and st_cond and br_ok)
            elif cid == 10:
                adxth = float(ent.get('adx', 22.0))
                upper_w = h - max(c, o)
                lower_w = min(c, o) - l
                bull_pin = lower_w > body15 * 1.5 and c > o
                bear_pin = upper_w > body15 * 1.5 and c < o
                cond_l = bool(math.isfinite(ao15) and ao15 > 0.0 and math.isfinite(sq15) and (sq15 > 0.0) and math.isfinite(z15) and (z15 < -1.5) and tu100_15 and math.isfinite(adx15) and (adx15 > adxth) and bull_pin)
                cond_s = bool(math.isfinite(ao15) and ao15 < 0.0 and math.isfinite(sq15) and (sq15 < 0.0) and math.isfinite(z15) and (z15 > 1.5) and td100_15 and math.isfinite(adx15) and (adx15 > adxth) and bear_pin)
            edge_l = cond_l and (not self.prev_cond_L[combo.combo_key])
            edge_s = cond_s and (not self.prev_cond_S[combo.combo_key])
            self.prev_cond_L[combo.combo_key] = cond_l
            self.prev_cond_S[combo.combo_key] = cond_s
            if edge_l:
                ok = bool(approved('L', smc15, ob15_buy, combo.layer2))
                raw_candidates.append((combo, 'L', open_ms, smc15, ob15_buy, ok))
            if edge_s:
                ok = bool(approved('S', smc15, ob15_sell, combo.layer2))
                raw_candidates.append((combo, 'S', open_ms, smc15, ob15_sell, ok))
        h1_closed, h1_open_ms, o1, h1, l1, c1, v1 = self.agg_1h.update(open_ms, o, h, l, c, v)
        if h1_closed:
            a21_1 = self.h1_atr21.step(h1, l1, c1)
            a14_1 = self.h1_atr14.step(h1, l1, c1)
            a20_1 = self.h1_atr20.step(h1, l1, c1)
            r1 = self.h1_rsi14.step(c1)
            rav1 = self.h1_rsi_sma14.step(r1)
            m1 = self.h1_mfi14.step(h1, l1, c1, v1)
            dip1, dim1, adx1 = self.h1_dmi14.step(h1, l1, c1)
            volma1 = self.h1_vol_sma20.step(v1)
            k11, d11 = self.h1_stoch11.step(h1, l1, c1)
            k14, d14 = self.h1_stoch14.step(h1, l1, c1)
            cci9 = self.h1_cci9.step(c1)
            macd8 = self.h1_ema8.step(c1) - self.h1_ema21.step(c1)
            sig8 = self.h1_macd8_sig9.step(macd8)
            hist8 = macd8 - sig8
            e10_1 = self.h1_ema10.step(c1)
            e25_1 = self.h1_ema25.step(c1)
            e42_1 = self.h1_ema42.step(c1)
            tfast1 = self.h1_ema21.val if self.h1_ema21.total_count >= 21 else np.nan
            tslow1 = e42_1
            basis20_1 = self.h1_c_sma20.step(c1)
            std20_1 = self.h1_c_std20.step(c1)
            dev18_1 = 1.8 * std20_1 if math.isfinite(std20_1) else np.nan
            bbup1 = math.isfinite(basis20_1) and math.isfinite(dev18_1) and (c1 > basis20_1 + dev18_1)
            bbdn1 = math.isfinite(basis20_1) and math.isfinite(dev18_1) and (c1 < basis20_1 - dev18_1)
            ats1, above1, below1 = self.h1_ats.step(c1, a14_1)
            stdir1 = self.h1_st_custom.step(h1, l1, c1)
            sar1 = self.h1_psar.step(h1, l1, c1)
            bull_eng1 = math.isfinite(self.h1_prev_c) and c1 > o1 and (self.h1_prev_c < self.h1_prev_o) and (c1 > self.h1_prev_o) and (o1 < self.h1_prev_c)
            bear_eng1 = math.isfinite(self.h1_prev_c) and c1 < o1 and (self.h1_prev_c > self.h1_prev_o) and (c1 < self.h1_prev_o) and (o1 > self.h1_prev_c)
            if math.isfinite(self.h1_prev_c):
                self.h1_obv += (c1 - self.h1_prev_c) * v1
                self.h1_obv_seeded = True
            cur_obv = self.h1_obv if self.h1_obv_seeded else np.nan
            oup1 = math.isfinite(cur_obv) and math.isfinite(self.h1_prev_obv) and (cur_obv > self.h1_prev_obv)
            odn1 = math.isfinite(cur_obv) and math.isfinite(self.h1_prev_obv) and (cur_obv < self.h1_prev_obv)
            self.h1_prev_obv = cur_obv
            bbu1 = basis20_1 + 2.0 * std20_1 if math.isfinite(basis20_1) and math.isfinite(std20_1) else np.nan
            bbl1 = basis20_1 - 2.0 * std20_1 if math.isfinite(basis20_1) and math.isfinite(std20_1) else np.nan
            ku1 = basis20_1 + 1.7 * a20_1 if math.isfinite(basis20_1) and math.isfinite(a20_1) else np.nan
            kl1 = basis20_1 - 1.7 * a20_1 if math.isfinite(basis20_1) and math.isfinite(a20_1) else np.nan
            breakout_b1 = math.isfinite(bbu1) and math.isfinite(self.h1_prev_bbu) and math.isfinite(self.h1_prev_c) and (c1 > bbu1) and (self.h1_prev_c <= self.h1_prev_bbu) and math.isfinite(ku1) and (c1 > ku1)
            breakout_s1 = math.isfinite(bbl1) and math.isfinite(self.h1_prev_bbl) and math.isfinite(self.h1_prev_c) and (c1 < bbl1) and (self.h1_prev_c >= self.h1_prev_bbl) and math.isfinite(kl1) and (c1 < kl1)
            self.h1_prev_bbu = bbu1
            self.h1_prev_bbl = bbl1
            day_id = h1_open_ms // DAY_MS
            if self.h1_cur_day != day_id:
                self.h1_cur_day = day_id
                self.h1_vwap_pv = 0.0
                self.h1_vwap_vol = 0.0
            tp1 = (h1 + l1 + c1) / 3.0
            self.h1_vwap_pv += tp1 * v1
            self.h1_vwap_vol += v1
            vwap1 = self.h1_vwap_pv / self.h1_vwap_vol if self.h1_vwap_vol != 0.0 else np.nan
            macd12 = self.h1_ema12.step(c1) - self.h1_ema26.step(c1)
            sig12 = self.h1_macd12_sig9.step(macd12)
            hist12 = macd12 - sig12
            e9_1 = self.h1_ema9.step(c1)
            sma8_1 = self.h1_c_sma8.step(c1)
            sup1 = sma8_1 + 2.0 * a20_1 if math.isfinite(sma8_1) and math.isfinite(a20_1) else np.nan
            strong1 = math.isfinite(a20_1) and abs(c1 - o1) > a20_1 * 0.8
            z1 = (c1 - basis20_1) / std20_1 if math.isfinite(std20_1) and std20_1 != 0.0 else np.nan
            ao1 = self.h1_ema5.step(c1) - self.h1_ema34.step(c1)
            sq1 = self.h1_ema20.step(c1) - self.h1_ema50.step(c1)
            prev_max20 = max(self.h1_c_win20) if len(self.h1_c_win20) == 20 else np.nan
            prev_min20 = min(self.h1_c_win20) if len(self.h1_c_win20) == 20 else np.nan
            pcL1 = math.isfinite(prev_max20) and c1 > prev_max20
            pcS1 = math.isfinite(prev_min20) and c1 < prev_min20
            self.h1_c_win20.append(c1)
            if len(self.h1_c_win20) > 20:
                self.h1_c_win20.pop(0)
            smc1 = self.h1_smc.step(o1, h1, l1, c1)
            ob1_buy, ob1_sell = self.h1_ob.step(o1, h1, l1, c1)
            volsp1 = math.isfinite(volma1) and v1 > volma1 * 1.5
            adxr1 = math.isfinite(adx1) and math.isfinite(self.h1_prev_adx) and (adx1 > self.h1_prev_adx)
            sb08_1 = c1 > o1 and math.isfinite(a21_1) and (c1 - o1 > a21_1 * 0.8)
            ss08_1 = c1 < o1 and math.isfinite(a21_1) and (o1 - c1 > a21_1 * 0.8)
            tu150_4h = math.isfinite(cur_ema4h_150) and c1 > cur_ema4h_150
            td150_4h = math.isfinite(cur_ema4h_150) and c1 < cur_ema4h_150
            cci_up = math.isfinite(cci9) and math.isfinite(self.h1_prev_cci9) and (cci9 > self.h1_prev_cci9)
            cci_dn = math.isfinite(cci9) and math.isfinite(self.h1_prev_cci9) and (cci9 < self.h1_prev_cci9)
            longc1 = math.isfinite(k11) and math.isfinite(d11) and (k11 > 30.0) and (k11 > d11) and (cci9 > 80.0) and cci_up
            shortc1 = math.isfinite(k11) and math.isfinite(d11) and (k11 < 70.0) and (k11 < d11) and (cci9 > -80.0) and cci_dn
            bt1 = math.isfinite(tfast1) and math.isfinite(tslow1) and (tfast1 > tslow1)
            br1 = math.isfinite(tfast1) and math.isfinite(tslow1) and (tfast1 < tslow1)
            for combo in self.combos:
                if combo.native_timeframe != '1h':
                    continue
                cid = combo.combo_id
                ent = combo.entry
                if not combo.is_new6:
                    cond_l = False
                    cond_s = False
                    if cid == 1:
                        lth = float(ent.get('mfi_long', 50.0))
                        sth = float(ent.get('mfi_short', 50.0))
                        cond_l = bool(longc1 and math.isfinite(r1) and (r1 > 55.0) and math.isfinite(hist8) and (hist8 > 0.0) and math.isfinite(m1) and (m1 > lth) and adxr1 and sb08_1 and tu150_4h and volsp1)
                        cond_s = bool(shortc1 and math.isfinite(r1) and (r1 < 45.0) and math.isfinite(hist8) and (hist8 < 0.0) and math.isfinite(m1) and (m1 < sth) and adxr1 and ss08_1 and td150_4h and volsp1)
                    elif cid == 2:
                        adxth = float(ent.get('adx', 20.0))
                        cond_l = bool(math.isfinite(ats1) and c1 > ats1 and above1 and math.isfinite(e10_1) and math.isfinite(e25_1) and (e10_1 > e25_1) and math.isfinite(macd8) and math.isfinite(sig8) and (macd8 > sig8) and math.isfinite(adx1) and (adx1 > adxth) and bbup1)
                        cond_s = bool(math.isfinite(ats1) and c1 < ats1 and below1 and math.isfinite(e10_1) and math.isfinite(e25_1) and (e10_1 < e25_1) and math.isfinite(macd8) and math.isfinite(sig8) and (macd8 < sig8) and math.isfinite(adx1) and (adx1 > adxth) and bbdn1)
                    elif cid == 3:
                        adxth = float(ent.get('adx', 34.0))
                        cond_l = bool(stdir1 == 1 and math.isfinite(adx1) and (adx1 > adxth) and math.isfinite(sar1) and (c1 > sar1) and bull_eng1 and oup1)
                        cond_s = bool(stdir1 == -1 and math.isfinite(adx1) and (adx1 > adxth) and math.isfinite(sar1) and (c1 < sar1) and bear_eng1 and odn1)
                    elif cid == 4:
                        adxth = float(ent.get('adx', 23.0))
                        cond_l = bool(bt1 and breakout_b1 and math.isfinite(cur_ema6h_50) and (c1 > cur_ema6h_50) and (stdir1 == 1) and sb08_1 and volsp1 and math.isfinite(adx1) and (adx1 > adxth))
                        cond_s = bool(br1 and breakout_s1 and math.isfinite(cur_ema6h_50) and (c1 < cur_ema6h_50) and (stdir1 == -1) and ss08_1 and volsp1 and math.isfinite(adx1) and (adx1 > adxth))
                    elif cid == 6:
                        bm = float(ent.get('body_atr', 1.4))
                        lc6 = math.isfinite(r1) and r1 > 30.0 and math.isfinite(k11) and math.isfinite(d11) and (k11 > 30.0) and (k11 > d11) and (cci9 > 100.0) and cci_up
                        sc6 = math.isfinite(r1) and r1 < 70.0 and math.isfinite(k11) and math.isfinite(d11) and (k11 < 70.0) and (k11 < d11) and (cci9 > -100.0) and cci_dn
                        adxs = math.isfinite(adx1) and adx1 > 20.0 and adxr1
                        sb = c1 > o1 and math.isfinite(a21_1) and (c1 - o1 > a21_1 * bm)
                        ss = c1 < o1 and math.isfinite(a21_1) and (o1 - c1 > a21_1 * bm)
                        cond_l = bool(sb and breakout_b1 and lc6 and adxs)
                        cond_s = bool(ss and breakout_s1 and sc6 and adxs)
                    elif cid == 11:
                        adxth = float(ent.get('adx', 30.0))
                        t2 = float(ent.get('tier2_score', 7.0))
                        t3 = float(ent.get('tier3_score', 5.0))
                        vm = float(ent.get('volume_mult', 1.3))
                        vs = math.isfinite(volma1) and v1 > volma1 * vm
                        tdL = math.isfinite(dip1) and math.isfinite(dim1) and (dip1 > dim1) and (dip1 > 22.0)
                        tdS = math.isfinite(dip1) and math.isfinite(dim1) and (dip1 < dim1) and (dim1 > 22.0)
                        e50_up = math.isfinite(cur_ema4h_50) and math.isfinite(self.prev_confirmed_ema4h_50_1h) and (cur_ema4h_50 > self.prev_confirmed_ema4h_50_1h)
                        e50_dn = math.isfinite(cur_ema4h_50) and math.isfinite(self.prev_confirmed_ema4h_50_1h) and (cur_ema4h_50 < self.prev_confirmed_ema4h_50_1h)
                        trL = math.isfinite(cur_ema4h_50) and math.isfinite(cur_ema4h_200) and (cur_ema4h_50 > cur_ema4h_200) and tdL and math.isfinite(vwap1) and (c1 > vwap1) and e50_up
                        trS = math.isfinite(cur_ema4h_50) and math.isfinite(cur_ema4h_200) and (cur_ema4h_50 < cur_ema4h_200) and tdS and math.isfinite(vwap1) and (c1 < vwap1) and e50_dn
                        t1L = trL and vs and math.isfinite(adx1) and (adx1 > adxth)
                        t1S = trS and vs and math.isfinite(adx1) and (adx1 > adxth)
                        k14_x_up = math.isfinite(k14) and math.isfinite(d14) and math.isfinite(self.h1_prev_k14) and math.isfinite(self.h1_prev_d14) and (k14 > d14) and (self.h1_prev_k14 <= self.h1_prev_d14)
                        k14_x_dn = math.isfinite(k14) and math.isfinite(d14) and math.isfinite(self.h1_prev_k14) and math.isfinite(self.h1_prev_d14) and (k14 < d14) and (self.h1_prev_k14 >= self.h1_prev_d14)
                        a20_up = math.isfinite(a20_1) and math.isfinite(self.h1_prev_atr20) and (a20_1 > self.h1_prev_atr20)
                        k14_up50 = math.isfinite(k14) and math.isfinite(self.h1_prev_k14) and (k14 > self.h1_prev_k14) and (k14 > 50.0)
                        k14_dn50 = math.isfinite(k14) and math.isfinite(self.h1_prev_k14) and (k14 < self.h1_prev_k14) and (k14 < 50.0)
                        s2L = (2.0 if math.isfinite(hist12) and hist12 > 0.0 else 0.0) + (2.0 if math.isfinite(cur_ema4h_150) and math.isfinite(cur_ema4h_200) and (cur_ema4h_150 > cur_ema4h_200) else 0.0) + (1.0 if math.isfinite(cci9) and cci9 > 100.0 else 0.0) + (1.0 if math.isfinite(r1) and r1 > 50.0 else 0.0) + (1.0 if math.isfinite(sup1) and c1 > sup1 else 0.0) + (0.5 if k14_x_up else 0.0) + (1.0 if math.isfinite(tfast1) and c1 > tfast1 else 0.0) + (1.0 if strong1 and c1 > o1 else 0.0) + (1.0 if math.isfinite(m1) and m1 > 55.0 else 0.0) + (1.0 if a20_up else 0.0) + (1.0 if math.isfinite(e9_1) and math.isfinite(tfast1) and (e9_1 > tfast1) else 0.0) + (1.0 if cci_up and cci9 > 100.0 else 0.0) + (1.0 if k14_up50 else 0.0)
                        s2S = (2.0 if math.isfinite(hist12) and hist12 < 0.0 else 0.0) + (2.0 if math.isfinite(cur_ema4h_150) and math.isfinite(cur_ema4h_200) and (cur_ema4h_150 < cur_ema4h_200) else 0.0) + (1.0 if math.isfinite(cci9) and cci9 < -100.0 else 0.0) + (1.0 if math.isfinite(r1) and r1 < 50.0 else 0.0) + (1.0 if math.isfinite(sup1) and c1 < sup1 else 0.0) + (0.5 if k14_x_dn else 0.0) + (1.0 if math.isfinite(tfast1) and c1 < tfast1 else 0.0) + (1.0 if strong1 and c1 < o1 else 0.0) + (1.0 if math.isfinite(m1) and m1 < 45.0 else 0.0) + (1.0 if a20_up else 0.0) + (1.0 if math.isfinite(e9_1) and math.isfinite(tfast1) and (e9_1 < tfast1) else 0.0) + (1.0 if cci_dn and cci9 < -100.0 else 0.0) + (1.0 if k14_dn50 else 0.0)
                        mrL = math.isfinite(m1) and math.isfinite(self.h1_prev_mfi) and (m1 > 60.0) and (m1 > self.h1_prev_mfi) and vs
                        mrS = math.isfinite(m1) and math.isfinite(self.h1_prev_mfi) and (m1 < 40.0) and (m1 < self.h1_prev_mfi) and vs
                        s3L = (1.0 if math.isfinite(z1) and z1 < -1.5 else 0.0) + (1.0 if math.isfinite(ao1) and ao1 > 0.0 else 0.0) + (1.0 if math.isfinite(sq1) and sq1 > 0.0 else 0.0) + (1.0 if math.isfinite(r1) and math.isfinite(rav1) and (r1 > rav1) else 0.0) + (1.0 if math.isfinite(e9_1) and math.isfinite(tfast1) and (e9_1 > tfast1) else 0.0) + (1.0 if math.isfinite(hist12) and hist12 > 0.0 else 0.0) + (1.0 if stdir1 == 1 else 0.0) + (1.0 if pcL1 else 0.0) + (1.0 if mrL else 0.0) + (1.0 if math.isfinite(r1) and r1 > 50.0 else 0.0) + (1.0 if vs else 0.0)
                        s3S = (1.0 if math.isfinite(z1) and z1 > 1.5 else 0.0) + (1.0 if math.isfinite(ao1) and ao1 < 0.0 else 0.0) + (1.0 if math.isfinite(sq1) and sq1 < 0.0 else 0.0) + (1.0 if math.isfinite(r1) and math.isfinite(rav1) and (r1 < rav1) else 0.0) + (1.0 if math.isfinite(e9_1) and math.isfinite(tfast1) and (e9_1 < tfast1) else 0.0) + (1.0 if math.isfinite(hist12) and hist12 < 0.0 else 0.0) + (1.0 if stdir1 == -1 else 0.0) + (1.0 if pcS1 else 0.0) + (1.0 if mrS else 0.0) + (1.0 if math.isfinite(r1) and r1 < 50.0 else 0.0) + (1.0 if vs else 0.0)
                        cond_l = bool(t1L and s2L >= t2 and (s3L >= t3))
                        cond_s = bool(t1S and s2S >= t2 and (s3S >= t3))
                    edge_l = cond_l and (not self.prev_cond_L[combo.combo_key])
                    edge_s = cond_s and (not self.prev_cond_S[combo.combo_key])
                    self.prev_cond_L[combo.combo_key] = cond_l
                    self.prev_cond_S[combo.combo_key] = cond_s
                    if edge_l:
                        ok = bool(approved('L', smc1, ob1_buy, combo.layer2))
                        raw_candidates.append((combo, 'L', h1_open_ms, smc1, ob1_buy, ok))
                    if edge_s:
                        ok = bool(approved('S', smc1, ob1_sell, combo.layer2))
                        raw_candidates.append((combo, 'S', h1_open_ms, smc1, ob1_sell, ok))
                else:
                    ns = self.new6_states[combo.combo_key]
                    raw_l = False
                    raw_s = False
                    if cid == 101:
                        z_dm = ns['ind'].step(h1, l1)
                        long_min = float(ent.get('long_min', 0.75))
                        short_max = float(ent.get('short_max', 0.25))
                        raw_l = math.isfinite(z_dm) and z_dm > long_min
                        raw_s = math.isfinite(z_dm) and z_dm < short_max
                    elif cid == 102:
                        up_a, dn_a = ns['ind'].step(h1, l1)
                        th = float(ent.get('threshold', 70.0))
                        prev_up, prev_dn = (ns['prev_up'], ns['prev_dn'])
                        x_up = math.isfinite(up_a) and math.isfinite(dn_a) and math.isfinite(prev_up) and math.isfinite(prev_dn) and (up_a > dn_a) and (prev_up <= prev_dn)
                        x_dn = math.isfinite(up_a) and math.isfinite(dn_a) and math.isfinite(prev_up) and math.isfinite(prev_dn) and (dn_a > up_a) and (prev_dn <= prev_up)
                        raw_l = x_up and up_a >= th
                        raw_s = x_dn and dn_a >= th
                        ns['prev_up'], ns['prev_dn'] = (up_a, dn_a)
                    elif cid == 103:
                        z_cmo = ns['ind'].step(c1)
                        th = float(ent.get('threshold', 40.0))
                        prev_z = ns['prev_z']
                        raw_l = math.isfinite(z_cmo) and math.isfinite(prev_z) and (z_cmo > th) and (prev_z <= th)
                        raw_s = math.isfinite(z_cmo) and math.isfinite(prev_z) and (z_cmo < -th) and (prev_z >= -th)
                        ns['prev_z'] = z_cmo
                    elif cid == 104:
                        tenkan, kijun, span_a, span_b = ns['ind'].step(h1, l1)
                        prev_t, prev_k = (ns['prev_t'], ns['prev_k'])
                        x_up = math.isfinite(tenkan) and math.isfinite(kijun) and math.isfinite(prev_t) and math.isfinite(prev_k) and (tenkan > kijun) and (prev_t <= prev_k)
                        x_dn = math.isfinite(tenkan) and math.isfinite(kijun) and math.isfinite(prev_t) and math.isfinite(prev_k) and (tenkan < kijun) and (prev_t >= prev_k)
                        if math.isfinite(span_a) and math.isfinite(span_b):
                            top = max(span_a, span_b)
                            bot = min(span_a, span_b)
                            raw_l = x_up and c1 > top and (span_a > span_b)
                            raw_s = x_dn and c1 < bot and (span_a < span_b)
                        ns['prev_t'], ns['prev_k'] = (tenkan, kijun)
                    elif cid == 105:
                        kama, er, slope = ns['ind'].step(c1)
                        er_min = float(ent.get('er_min', 0.4))
                        raw_l = math.isfinite(kama) and math.isfinite(slope) and math.isfinite(er) and (c1 > kama) and (slope > 0.0) and (er > er_min)
                        raw_s = math.isfinite(kama) and math.isfinite(slope) and math.isfinite(er) and (c1 < kama) and (slope < 0.0) and (er > er_min)
                    elif cid == 106:
                        vip, vim = ns['ind'].step(h1, l1, c1)
                        ratio = float(ent.get('ratio', 1.2))
                        prev_vip, prev_vim = (ns['prev_vip'], ns['prev_vim'])
                        x_up = math.isfinite(vip) and math.isfinite(vim) and math.isfinite(prev_vip) and math.isfinite(prev_vim) and (vip > vim) and (prev_vip <= prev_vim)
                        x_dn = math.isfinite(vip) and math.isfinite(vim) and math.isfinite(prev_vip) and math.isfinite(prev_vim) and (vim > vip) and (prev_vim <= prev_vip)
                        raw_l = x_up and vip > vim * ratio
                        raw_s = x_dn and vim > vip * ratio
                        ns['prev_vip'], ns['prev_vim'] = (vip, vim)
                    if raw_l != raw_s:
                        side_code = 'L' if raw_l else 'S'
                        blocked = ob1_buy if raw_l else ob1_sell
                        ok = bool(approved(side_code, smc1, blocked, combo.layer2))
                        raw_candidates.append((combo, side_code, h1_open_ms, smc1, blocked, ok))
            self.h1_prev_adx = adx1
            self.h1_prev_cci9 = cci9
            self.h1_prev_c = c1
            self.h1_prev_o = o1
            self.h1_prev_atr20 = a20_1
            self.h1_prev_mfi = m1
            self.h1_prev_k14 = k14
            self.h1_prev_d14 = d14
            self.prev_confirmed_ema4h_50_1h = cur_ema4h_50
        h4_closed, _, _, _, _, c4, _ = self.agg_4h.update(open_ms, o, h, l, c, v)
        if h4_closed:
            self.confirmed_ema4h_50 = self.ema4h_50.step(c4)
            self.confirmed_ema4h_100 = self.ema4h_100.step(c4)
            self.confirmed_ema4h_150 = self.ema4h_150.step(c4)
            self.confirmed_ema4h_200 = self.ema4h_200.step(c4)
        h6_closed, _, _, _, _, c6, _ = self.agg_6h.update(open_ms, o, h, l, c, v)
        if h6_closed:
            self.confirmed_ema6h_50 = self.ema6h_50.step(c6)
        events: list[SignalEvent] = []
        close_ms = open_ms + M15_MS - 1
        sym_clean = self.symbol.replace('-', '').replace('_', '')
        warmup_ready = pos >= self.warmup_bars - 1
        for combo, side_code, nat_open_ms, sd, blocked, ok in raw_candidates:
            if only_approved and (not ok):
                continue
            if only_warmup_ready and (not warmup_ready):
                continue
            side_str = 'BUY' if side_code == 'L' else 'SELL'
            signal_id = f'SIG_{sym_clean}_{combo.combo_name}_{combo.native_timeframe}_{side_str}_{close_ms}'
            events.append(SignalEvent(signal_id=signal_id, combo_key=combo.combo_key, symbol=self.symbol, side=side_str, native_open_ms=nat_open_ms, signal_close_ms=close_ms, ref_price=float(c), tags={'smc_dir': int(sd), 'ob_blocked': bool(blocked), 'approved': bool(ok), 'warmup_ready': bool(warmup_ready), 'combo_id': int(combo.combo_id), 'combo_name': combo.combo_name, 'timeframe': combo.native_timeframe, 'bar_position': int(pos), 'bar_open_ms': int(open_ms), 'provider': provider, 'is_new6': bool(combo.is_new6)}))
        events.sort(key=lambda e: (e.signal_close_ms, int(e.tags['combo_id']), e.side))
        return events

# --- from autotrade_v3/strategy_core/strategies/final20/logic.py
class Final20LiveState:
    """Multi-symbol live incremental state container for Final20Strategy.on_bar_close()."""

    def __init__(self, spec: StrategySpec, only_approved: bool=True, only_warmup_ready: bool=True):
        self.spec = spec
        self.only_approved = only_approved
        self.only_warmup_ready = only_warmup_ready
        self.symbol_states: dict[str, SymbolLiveState] = {}

    def get_symbol_state(self, symbol: str) -> SymbolLiveState:
        sym_canonical = 'BTC-USDT' if 'BTC' in symbol.upper() else 'ETH-USDT' if 'ETH' in symbol.upper() else symbol
        if sym_canonical not in self.symbol_states:
            sym_combos = [c for c in self.spec.combos if c.symbol.upper() == sym_canonical.upper()]
            self.symbol_states[sym_canonical] = SymbolLiveState(sym_canonical, sym_combos, self.spec.warmup_bars)
        return self.symbol_states[sym_canonical]

    def on_bar_close(self, bar: Any, only_approved: bool | None=None, only_warmup_ready: bool | None=None) -> list[SignalEvent]:
        if isinstance(bar, dict):
            sym = str(bar.get('symbol', 'BTC-USDT'))
        elif hasattr(bar, 'get'):
            sym = str(bar.get('symbol', 'BTC-USDT'))
        else:
            sym = str(getattr(bar, 'symbol', 'BTC-USDT'))
        s_state = self.get_symbol_state(sym)
        evs = s_state.step_bar_dict(bar, only_approved=self.only_approved if only_approved is None else only_approved, only_warmup_ready=self.only_warmup_ready if only_warmup_ready is None else only_warmup_ready)
        if sym != s_state.symbol:
            evs = [SignalEvent(signal_id=e.signal_id, combo_key=e.combo_key, symbol=sym, side=e.side, native_open_ms=e.native_open_ms, signal_close_ms=e.signal_close_ms, ref_price=e.ref_price, tags=e.tags) for e in evs]
        return evs

# --- from autotrade_v3/strategy_core/registry.py
PARAMS_DIR = FINAL20_DIR / "params"

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

    if sid.upper() in ("20FINAL_BASELINE", "20FINAL"):
        sid = "20FINAL"
        if ver in ("1.0.0", "1.0", ""):
            ver = "1"
    return sid, ver

# --- from autotrade_v3/strategy_core/registry.py
def compute_param_diff(
    source_params: dict[str, Any],
    target_params: dict[str, Any],
) -> dict[str, Any]:
    """Compute a deterministic parameter diff between source and target strategy parameter dicts."""
    src_combos = {str(c["combo_key"]): c for c in source_params.get("combos", [])}
    tgt_combos = {str(c["combo_key"]): c for c in target_params.get("combos", [])}

    added_combos = sorted(set(tgt_combos.keys()) - set(src_combos.keys()))
    removed_combos = sorted(set(src_combos.keys()) - set(tgt_combos.keys()))
    modified_combos: dict[str, dict[str, Any]] = {}

    for k in sorted(set(src_combos.keys()) & set(tgt_combos.keys())):
        sc = src_combos[k]
        tc = tgt_combos[k]
        if sc != tc:
            field_changes = {}
            for fk in sorted(set(sc.keys()) | set(tc.keys())):
                if sc.get(fk) != tc.get(fk):
                    field_changes[fk] = {"from": sc.get(fk), "to": tc.get(fk)}
            modified_combos[k] = field_changes

    top_changes = {}
    for fk in ("warmup_bars", "timeframes"):
        if source_params.get(fk) != target_params.get(fk):
            top_changes[fk] = {"from": source_params.get(fk), "to": target_params.get(fk)}

    return {
        "from_strategy": f"{source_params.get('strategy_id')}@{source_params.get('version')}",
        "to_strategy": f"{target_params.get('strategy_id')}@{target_params.get('version')}",
        "top_level_changes": top_changes,
        "added_combos": [tgt_combos[k] for k in added_combos],
        "removed_combos": removed_combos,
        "modified_combos": modified_combos,
    }

# --- from autotrade_v3/strategy_core/registry.py
def clone_strategy_params(
    from_ref: str,
    to_ref: str,
    params_dir: Path | None = None,
    combo_overrides: dict[str, dict[str, Any]] | None = None,
    add_combos: list[dict[str, Any]] | None = None,
    remove_combos: list[str] | None = None,
) -> dict[str, Any]:
    """Clone an existing strategy parameter specification into a new versioned directory.

    Never modifies the baseline `20final_v1.json` or `20final_v1.lock`.
    Creates `<params_dir>/<to_id_lower>_v<to_ver>/params.json` and `param_diff.json`.
    """
    src_id, src_ver = parse_strategy_ref(from_ref)
    dst_id, dst_ver = parse_strategy_ref(to_ref)

    if (dst_id, dst_ver) == ("20FINAL", "1"):
        raise ValueError("Cannot clone into immutable baseline 20FINAL@1")
    if (dst_id, dst_ver) == (src_id, src_ver):
        raise ValueError(f"Cannot clone strategy onto itself: {dst_id}@{dst_ver}")

    p_dir = Path(params_dir) if params_dir is not None else PARAMS_DIR
    dst_slug = f"{dst_id.lower()}_v{dst_ver}"
    out_dir = p_dir / dst_slug
    existing_candidates = [
        out_dir / "params.json",
        p_dir / f"{dst_slug}.json",
        p_dir / f"{dst_id}_v{dst_ver}.json",
    ]
    if out_dir.exists() or any(c.exists() for c in existing_candidates):
        raise FileExistsError(
            f"Target strategy '{dst_id}@{dst_ver}' already exists under {p_dir}; "
            "refusing to overwrite existing strategy parameters."
        )

    # Locate source params file
    if (src_id, src_ver) == ("20FINAL", "1"):
        src_path = p_dir / "20final_v1.json"
        if not src_path.exists():
            src_path = DEFAULT_PARAMS_PATH
    else:
        src_slug = f"{src_id.lower()}_v{src_ver}"
        src_path = p_dir / src_slug / "params.json"
        if not src_path.exists():
            src_path = p_dir / f"{src_slug}.json"
    if not src_path.exists():
        raise FileNotFoundError(f"Source strategy parameters not found for {src_id}@{src_ver}: {src_path}")

    src_data = json.loads(normalize_lf(src_path.read_text(encoding="utf-8")))
    dst_data = copy.deepcopy(src_data)
    dst_data["strategy_id"] = dst_id
    dst_data["version"] = dst_ver
    dst_data["parent"] = f"{src_id}@{src_ver}"

    combos_list: list[dict[str, Any]] = dst_data.get("combos", [])
    if remove_combos:
        rm_set = set(remove_combos)
        combos_list = [c for c in combos_list if c["combo_key"] not in rm_set]

    if combo_overrides:
        for c in combos_list:
            ck = c["combo_key"]
            if ck in combo_overrides:
                ov = combo_overrides[ck]
                for k, v in ov.items():
                    if k in ("entry", "exit_policy") and isinstance(v, dict) and isinstance(c.get(k), dict):
                        c[k].update(v)
                    else:
                        c[k] = v

    if add_combos:
        existing_keys = {c["combo_key"] for c in combos_list}
        for ac in add_combos:
            if ac["combo_key"] in existing_keys:
                raise ValueError(f"Duplicate combo_key when adding combo: {ac['combo_key']}")
            combos_list.append(copy.deepcopy(ac))
            existing_keys.add(ac["combo_key"])

    dst_data["combos"] = combos_list

    diff_obj = compute_param_diff(src_data, dst_data)
    dst_data["param_diff"] = {
        "added_combos": [c["combo_key"] for c in diff_obj["added_combos"]],
        "removed_combos": diff_obj["removed_combos"],
        "modified_combos": diff_obj["modified_combos"],
        "top_level_changes": diff_obj["top_level_changes"],
    }

    out_dir.mkdir(parents=True, exist_ok=False)
    params_out = out_dir / "params.json"
    diff_out = out_dir / "param_diff.json"

    params_out.write_text(normalize_lf(json.dumps(dst_data, indent=2) + "\n"), encoding="utf-8", newline="\n")
    diff_out.write_text(normalize_lf(json.dumps(diff_obj, indent=2) + "\n"), encoding="utf-8", newline="\n")

    hashes = compute_all_hashes(dst_data)
    return {
        "strategy_id": dst_id,
        "version": dst_ver,
        "parent": f"{src_id}@{src_ver}",
        "params_path": str(params_out),
        "diff_path": str(diff_out),
        "hashes": hashes,
        "diff": diff_obj,
    }

# --- from autotrade_v3/strategy_core/registry.py
class StrategyCoreRegistry:
    """Registry indexing strategies by (strategy_id, version)."""

    _factories: dict[tuple[str, str], Callable[[], Strategy]] = {}
    _custom_instances: dict[tuple[str, str], Strategy] = {}

    @classmethod
    def register(
        cls,
        strategy: Strategy | Callable[[], Strategy],
        strategy_id: str | None = None,
        version: str | None = None,
    ) -> None:
        if isinstance(strategy, Strategy):
            sp = strategy.spec()
            key = (strategy_id or sp.strategy_id, version or sp.version)
            cls._custom_instances[key] = strategy
        else:
            if strategy_id is None or version is None:
                raise ValueError("strategy_id and version are required when registering a factory")
            cls._factories[(strategy_id, version)] = strategy

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

        p_dir = Path(params_dir) if params_dir is not None else PARAMS_DIR
        if key == ("20FINAL", "1"):
            p_path = p_dir / "20final_v1.json"
            if not p_path.exists():
                p_path = DEFAULT_PARAMS_PATH
            l_path = p_dir / "20final_v1.lock"
            if not l_path.exists():
                l_path = DEFAULT_LOCK_PATH
            return Final20Strategy(
                params_path=p_path,
                lock_path=l_path,
                enforce_lock=True,
            )

        slug = f"{sid.lower()}_v{ver}"
        candidates = [
            p_dir / slug / "params.json",
            p_dir / f"{slug}.json",
            p_dir / f"{sid}_v{ver}.json",
        ]
        for cand in candidates:
            if cand.exists():
                return Final20Strategy(
                    params_path=cand,
                    enforce_lock=False,
                )

        raise KeyError(
            f"Strategy '{sid}@{ver}' not found in StrategyCoreRegistry. "
            f"Registered: {cls.list_keys(params_dir=p_dir)}"
        )

    @classmethod
    def clone(
        cls,
        from_ref: str,
        to_ref: str,
        params_dir: Path | None = None,
        combo_overrides: dict[str, dict[str, Any]] | None = None,
        add_combos: list[dict[str, Any]] | None = None,
        remove_combos: list[str] | None = None,
    ) -> Strategy:
        res = clone_strategy_params(
            from_ref=from_ref,
            to_ref=to_ref,
            params_dir=params_dir,
            combo_overrides=combo_overrides,
            add_combos=add_combos,
            remove_combos=remove_combos,
        )
        return cls.get(res["strategy_id"], res["version"], params_dir=params_dir)

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

    @classmethod
    def list_strategies(cls, params_dir: Path | None = None) -> list[dict[str, Any]]:
        out = []
        for ref in cls.list_keys(params_dir=params_dir):
            strat = cls.get(ref, params_dir=params_dir)
            out.append(strat.spec().to_dict())
        return out

    @classmethod
    def clear(cls) -> None:
        cls._factories.clear()
        cls._custom_instances.clear()

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

# --- from autotrade_v3/runstore/hashing.py
def _json_default(obj: Any) -> Any:
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return str(obj)

# --- from autotrade_v3/runstore/hashing.py
def canonical_json(obj: Any) -> str:
    """Serialize *obj* to deterministic canonical JSON."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=_json_default)

# --- from autotrade_v3/runstore/hashing.py
def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

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

# --- from autotrade_v3/external/annotations.py
@dataclass(frozen=True)
class AnnotationRecord:
    source: str
    symbol: str
    as_of_ms: int
    payload: dict[str, Any]
    payload_hash: str
    as_of_unverified: bool

# --- from autotrade_v3/external/annotations.py
@dataclass(frozen=True)
class AnnotationQueryResult:
    records: list[AnnotationRecord]
    dropped_future_count: int
    has_unverified: bool

# --- from autotrade_v3/external/annotations.py
class AnnotationStore:
    """Persistent offline annotation store backed by DuckDB.

    Filters MUST only read via :meth:`query_as_of`, which strictly enforces
    ``as_of_ms <= signal_close_ms`` in SQL.
    """

    def __init__(self, db_path: str | Path = "runs/lab/annotations/annotations.duckdb") -> None:
        self.db_path = Path(db_path)

    def _ensure_table(self, con: Any) -> None:
        con.execute(_CREATE_SQL)

    def import_file(
        self,
        file_path: str | Path,
        *,
        source: str | None = None,
        as_of_verified: bool = True,
    ) -> dict[str, Any]:
        """Import annotations from a local JSON or CSV file.

        Rejects any row missing ``as_of_ms``. If ``as_of_verified=False``,
        marks imported rows with ``as_of_unverified=True`` (``AS_OF_UNVERIFIED``).
        """
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"Annotation file not found: {p}")

        if p.suffix.lower() == ".json":
            raw = json.loads(p.read_text(encoding="utf-8"))
            rows = raw["annotations"] if isinstance(raw, dict) and "annotations" in raw else raw
            if not isinstance(rows, list):
                raise ValueError("JSON annotation file must be a list of objects or {'annotations': [...]}")
        elif p.suffix.lower() == ".csv":
            df = pd.read_csv(p)
            rows = df.to_dict(orient="records")
        else:
            raise ValueError(f"Unsupported annotation file format: {p.suffix}")

        validated: list[tuple[str, str, int, str, str, bool]] = []
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"Row {idx} is not a dict")
            as_of_raw = row.get("as_of_ms")
            if as_of_raw is None or (isinstance(as_of_raw, float) and pd.isna(as_of_raw)) or str(as_of_raw).strip() == "":
                raise ValueError(f"Annotation row {idx} rejected: missing required as_of_ms")
            try:
                as_of_ms = int(as_of_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Annotation row {idx} rejected: invalid as_of_ms={as_of_raw!r}") from exc
            if as_of_ms <= 0:
                raise ValueError(f"Annotation row {idx} rejected: non-positive as_of_ms={as_of_ms}")

            row_source = str(row.get("source") or source or p.stem)
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                raise ValueError(f"Annotation row {idx} rejected: missing symbol")

            if "payload" in row and isinstance(row["payload"], dict):
                payload = row["payload"]
            elif "payload_json" in row and isinstance(row["payload_json"], str):
                payload = json.loads(row["payload_json"])
            else:
                payload = {
                    k: v
                    for k, v in row.items()
                    if k not in ("source", "symbol", "as_of_ms", "as_of_unverified") and not (isinstance(v, float) and pd.isna(v))
                }
            payload_json = canonical_json(payload)
            payload_hash = sha256_text(payload_json)
            row_unverified = (not bool(as_of_verified)) or bool(row.get("as_of_unverified", False))
            validated.append((row_source, symbol, as_of_ms, payload_json, payload_hash, row_unverified))

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        import duckdb  # lazy: lab annotation store only
        con = duckdb.connect(str(self.db_path))
        try:
            self._ensure_table(con)
            if validated:
                con.executemany(
                    "INSERT INTO annotations VALUES (?, ?, ?, ?, ?, ?)",
                    validated,
                )
        finally:
            con.close()

        return {
            "imported_count": len(validated),
            "as_of_unverified": not bool(as_of_verified) or any(r[5] for r in validated),
            "flag": "AS_OF_UNVERIFIED" if (not bool(as_of_verified) or any(r[5] for r in validated)) else "AS_OF_VERIFIED",
            "db_path": str(self.db_path),
        }

    def query_as_of(
        self,
        symbol: str,
        signal_close_ms: int,
        *,
        source: str | None = None,
    ) -> AnnotationQueryResult:
        """Return ONLY annotations with ``as_of_ms <= signal_close_ms``."""
        if not self.db_path.exists():
            return AnnotationQueryResult(records=[], dropped_future_count=0, has_unverified=False)

        cutoff = int(signal_close_ms)
        import duckdb  # lazy: lab annotation store only
        con = duckdb.connect(str(self.db_path), read_only=True)
        try:
            if source is not None:
                valid_rows = con.execute(
                    "SELECT source, symbol, as_of_ms, payload_json, payload_hash, as_of_unverified "
                    "FROM annotations WHERE symbol = ? AND source = ? AND as_of_ms <= ? "
                    "ORDER BY as_of_ms ASC",
                    [str(symbol), str(source), cutoff],
                ).fetchall()
                future_cnt = con.execute(
                    "SELECT COUNT(*) FROM annotations WHERE symbol = ? AND source = ? AND as_of_ms > ?",
                    [str(symbol), str(source), cutoff],
                ).fetchone()[0]
            else:
                valid_rows = con.execute(
                    "SELECT source, symbol, as_of_ms, payload_json, payload_hash, as_of_unverified "
                    "FROM annotations WHERE symbol = ? AND as_of_ms <= ? "
                    "ORDER BY as_of_ms ASC",
                    [str(symbol), cutoff],
                ).fetchall()
                future_cnt = con.execute(
                    "SELECT COUNT(*) FROM annotations WHERE symbol = ? AND as_of_ms > ?",
                    [str(symbol), cutoff],
                ).fetchone()[0]
        finally:
            con.close()

        records = [
            AnnotationRecord(
                source=str(r[0]),
                symbol=str(r[1]),
                as_of_ms=int(r[2]),
                payload=json.loads(r[3]),
                payload_hash=str(r[4]),
                as_of_unverified=bool(r[5]),
            )
            for r in valid_rows
        ]
        if future_cnt:
            logger.info(
                "Annotation guard dropped %d future rows (as_of_ms > %d) for symbol=%s",
                int(future_cnt),
                cutoff,
                symbol,
            )
        return AnnotationQueryResult(
            records=records,
            dropped_future_count=int(future_cnt),
            has_unverified=any(rec.as_of_unverified for rec in records),
        )

# --- from autotrade_v3/filters/base.py
@dataclass(frozen=True)
class FilterDecision:
    action: Literal["KEEP", "DROP"]
    reason: str = ""
    label: str = ""

# --- from autotrade_v3/filters/base.py
class FilterContext:
    """Causal context passed to filters.

    Filters have NO direct DB/filesystem access to annotations; they can ONLY
    call :meth:`get_annotations`, which strictly enforces ``as_of_ms <= signal.signal_close_ms``.
    """

    def __init__(self, annotation_store: AnnotationStore | None = None) -> None:
        self._annotation_store = annotation_store
        self.dropped_future_annotations: int = 0
        self.used_unverified_annotations: bool = False

    def get_annotations(
        self,
        symbol: str,
        signal_close_ms: int,
        *,
        source: str | None = None,
    ) -> AnnotationQueryResult:
        if self._annotation_store is None:
            return AnnotationQueryResult(records=[], dropped_future_count=0, has_unverified=False)
        res = self._annotation_store.query_as_of(symbol, int(signal_close_ms), source=source)
        self.dropped_future_annotations += res.dropped_future_count
        if res.has_unverified:
            self.used_unverified_annotations = True
        return res

# --- from autotrade_v3/filters/base.py
def hash_filter_config(cfg: dict[str, Any]) -> str:
    return sha256_text(canonical_json(cfg))

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py
C12_SYMBOL = 'ETH-USDT'

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py
C12_COMBO_ID = 102

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py
C12_V3_ATR_PERIOD = 14

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py
C12_V3_RET_PERIOD = 24

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py
C12_V3_ATR_PCT_MIN = 0.65

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py
C12_V3_RET_24H_MAX = 2.2

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py
H1_MS = 3600000

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py
def _index_to_utc_ms__c12_v3_atr065_no_ext24h(idx: pd.DatetimeIndex) -> np.ndarray:
    idx_utc = idx.tz_localize('UTC') if idx.tz is None else idx.tz_convert('UTC')
    idx_ms = idx_utc.as_unit('ms')
    return np.asarray(idx_ms.asi8, dtype=np.int64)

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py
def build_c12_1h_features_lookup(df_1h: pd.DataFrame, *, atr_period: int=C12_V3_ATR_PERIOD, ret_period: int=C12_V3_RET_PERIOD) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build strictly causal sorted arrays (close_ms, atr_pct_1h, ret_24h_1h) from closed 1H bars."""
    if df_1h.empty:
        return (np.array([], dtype=np.int64), np.array([], dtype=float), np.array([], dtype=float))
    d = df_1h.sort_index()
    h = d['high'].astype(float)
    l = d['low'].astype(float)
    c = d['close'].astype(float)
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr_sma = tr.rolling(int(atr_period)).mean()
    atr_pct = (atr_sma / c * 100.0).to_numpy(dtype=float)
    ret_24h = (c.pct_change(int(ret_period)) * 100.0).to_numpy(dtype=float)
    open_ms = _index_to_utc_ms__c12_v3_atr065_no_ext24h(pd.DatetimeIndex(d.index))
    close_ms = open_ms + H1_MS - 1
    return (close_ms, atr_pct, ret_24h)

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py
class C12V3Atr065NoExt24hFilter:
    """Locked ETH-USDT/C12 V3_ATR065_NO_EXT24H candle-only L2 filter."""
    name: str = 'ETH-USDT_C12_V3_ATR065_NO_EXT24H'

    def __init__(self, close_ms: np.ndarray, atr_pct: np.ndarray, ret_24h: np.ndarray, *, atr_pct_min: float=C12_V3_ATR_PCT_MIN, ret_24h_max: float=C12_V3_RET_24H_MAX) -> None:
        self._close_ms = close_ms
        self._atr_pct = atr_pct
        self._ret_24h = ret_24h
        self.atr_pct_min = float(atr_pct_min)
        self.ret_24h_max = float(ret_24h_max)

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        ok, reason = self.evaluate_signal(signal)
        return FilterDecision(action='KEEP' if ok else 'DROP', reason=reason, label=self.name)

    def evaluate_signal(self, sig) -> tuple[bool, str]:
        if sig.symbol != C12_SYMBOL or int(sig.combo_id) != C12_COMBO_ID:
            return (True, 'PASS_NON_C12')
        if len(self._close_ms) == 0:
            return (False, 'BLOCKED_V3_MISSING_1H')
        idx = int(np.searchsorted(self._close_ms, int(sig.signal_close_ms), side='right')) - 1
        if idx < 0:
            return (False, 'BLOCKED_V3_WARMUP_1H')
        atr_val = float(self._atr_pct[idx])
        ret_val = float(self._ret_24h[idx])
        if not (math.isfinite(atr_val) and math.isfinite(ret_val)):
            return (False, 'BLOCKED_V3_NONFINITE')
        if atr_val < self.atr_pct_min:
            return (False, 'BLOCKED_V3_LOW_ATR_1H')
        if ret_val > self.ret_24h_max:
            return (False, 'BLOCKED_V3_EXTENDED_24H')
        return (True, 'ALLOW_V3_ATR065_NO_EXT24H')

# --- from autotrade_v3/filters/c13_b1_vol_regime.py
C13_SYMBOL = "ETH-USDT"

# --- from autotrade_v3/filters/c13_b1_vol_regime.py
C13_COMBO_ID = 103

# --- from autotrade_v3/filters/c13_b1_vol_regime.py
C13_MAX_ATR_RATIO_50 = 1.50

# --- from autotrade_v3/filters/c13_b1_vol_regime.py
H1_MS = 3_600_000

# --- from autotrade_v3/filters/c13_b1_vol_regime.py
def build_c13_atr_ratio_lookup(df_eth_m15: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Compute closed-bar 1H atr_ratio_50 = ATR(14) / SMA(ATR(14), 50) lookup arrays."""
    d1 = resample_ohlcv(ensure_dt(df_eth_m15), "1h")
    if d1.empty:
        return np.array([], dtype=np.int64), np.array([], dtype=float)
    a14 = atr(d1, 14)
    atr_ratio_50 = (a14 / a14.rolling(50).mean()).to_numpy(dtype=float)
    close_ms_1h = (datetime_index_to_ms(d1.index) + H1_MS - 1).astype(np.int64)
    return close_ms_1h, atr_ratio_50

# --- from autotrade_v3/filters/c13_b1_vol_regime.py
class C13B1VolRegimeFilter:
    """Block ETH-USDT/C13 entries when 1H atr_ratio_50 exceeds 1.50 (protects 0.80% SL)."""

    name: str = "C13_B1_VOL_REGIME"

    def __init__(
        self,
        close_ms_1h: np.ndarray,
        atr_ratio_50: np.ndarray,
        max_atr_ratio_50: float = C13_MAX_ATR_RATIO_50,
    ) -> None:
        self.close_ms_1h = close_ms_1h
        self.atr_ratio_50 = atr_ratio_50
        self.max_atr_ratio_50 = float(max_atr_ratio_50)

    @classmethod
    def from_m15_dataframe(
        cls,
        df_eth_m15: pd.DataFrame,
        max_atr_ratio_50: float = C13_MAX_ATR_RATIO_50,
    ) -> C13B1VolRegimeFilter:
        close_ms_1h, atr_ratio_50 = build_c13_atr_ratio_lookup(df_eth_m15)
        return cls(close_ms_1h, atr_ratio_50, max_atr_ratio_50=max_atr_ratio_50)

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        return self.evaluate_signal(signal)

    def evaluate_signal(self, sig: Signal) -> FilterDecision:
        sig_ms = int(sig.signal_close_ms)
        idx1 = int(np.searchsorted(self.close_ms_1h, sig_ms, side="right")) - 1
        if idx1 < 0 or np.isnan(self.atr_ratio_50[idx1]):
            return FilterDecision(action="DROP", reason="BLOCKED_C13_WARMUP_1H", label=self.name)
        if float(self.atr_ratio_50[idx1]) > self.max_atr_ratio_50:
            return FilterDecision(action="DROP", reason="BLOCKED_C13_HIGH_ATR_RATIO_50", label=self.name)
        return FilterDecision(action="KEEP", reason="PASS", label=self.name)

# --- from autotrade_v3/filters/c14_h3a_kumo_band.py
C14_SYMBOL = "BTC-USDT"

# --- from autotrade_v3/filters/c14_h3a_kumo_band.py
C14_COMBO_ID = 104

# --- from autotrade_v3/filters/c14_h3a_kumo_band.py
H3A_MIN_KUMO_PCT_INCLUSIVE = 0.005

# --- from autotrade_v3/filters/c14_h3a_kumo_band.py
H3A_MAX_KUMO_PCT_EXCLUSIVE = 0.010

# --- from autotrade_v3/filters/c14_h3a_kumo_band.py
def build_c14_1h_kumo_lookup(df_1h: pd.DataFrame) -> dict[int, float]:
    """Build strictly causal ``{close_ms: abs(span_a - span_b) / close}`` from closed 1H bars."""
    if df_1h.empty:
        return {}
    d = df_1h.sort_index()
    h = d["high"].to_numpy(float)
    l = d["low"].to_numpy(float)
    c = d["close"].to_numpy(float)
    _, _, span_a, span_b = _ichimoku(h, l, conv=12, base=30, spanb=60)
    kumo_pct = np.where(c > 0.0, np.abs(span_a - span_b) / c, np.nan)

    idx_ns = d.index.tz_convert("UTC").asi8 if d.index.tz is not None else d.index.tz_localize("UTC").asi8
    close_ms = (idx_ns // 1_000_000) + 3_600_000 - 1
    return {int(cms): float(kp) for cms, kp in zip(close_ms, kumo_pct)}

# --- from autotrade_v3/filters/c14_h3a_kumo_band.py
class C14H3aKumoBandFilter:
    """Locked H3a V1 filter: admit BTC-USDT/C14 iff 0.005 <= abs(SpanA - SpanB)/Close < 0.010."""

    name: str = "BTC-USDT_C14_H3a_KUMO_BAND_V1"

    def __init__(self, kumo_by_close_ms: dict[int, float]) -> None:
        self._kumo_by_close_ms = dict(kumo_by_close_ms)
        # Locked parameters per BTC_C14_H3A_STANDARD_CANDIDATE.json
        self.min_inclusive: float = H3A_MIN_KUMO_PCT_INCLUSIVE
        self.max_exclusive: float = H3A_MAX_KUMO_PCT_EXCLUSIVE

    @classmethod
    def from_1h_dataframe(cls, df_1h: pd.DataFrame) -> "C14H3aKumoBandFilter":
        return cls(build_c14_1h_kumo_lookup(df_1h))

    def config_dict(self) -> dict[str, Any]:
        return {
            "filter": self.name,
            "symbol": C14_SYMBOL,
            "combo_id": C14_COMBO_ID,
            "min_inclusive": self.min_inclusive,
            "max_exclusive": self.max_exclusive,
            "locked": True,
        }

    def config_hash(self) -> str:
        return hash_filter_config(self.config_dict())

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        if str(getattr(signal, "symbol", "")) != C14_SYMBOL or int(getattr(signal, "combo_id", -1)) != C14_COMBO_ID:
            return FilterDecision(action="KEEP")

        cms = int(getattr(signal, "signal_close_ms"))
        kumo_pct = self._kumo_by_close_ms.get(cms)
        if kumo_pct is None:
            return FilterDecision(action="DROP", reason="H3a_MISSING_1H_BAR", label="H3a_DROP")
        if not math.isfinite(kumo_pct):
            return FilterDecision(action="DROP", reason="H3a_NONFINITE_INDICATOR", label="H3a_DROP")

        if self.min_inclusive <= kumo_pct < self.max_exclusive:
            return FilterDecision(action="KEEP", label="H3a_KEEP")
        return FilterDecision(action="DROP", reason="H3a_OUTSIDE_KUMO_BAND", label="H3a_DROP")

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py
C15_SYMBOL = 'ETH-USDT'

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py
C15_COMBO_ID = 105

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py
C15_V5_VARIANT_NAME = 'V5_SELL_ONLY_ATR140'

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py
C15_V5_ATR_PERIOD = 14

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py
C15_V5_ATR_SMA_PERIOD = 50

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py
C15_V5_ATR_RATIO_MAX = 1.4

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py
H1_MS = 3600000

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py
def _index_to_utc_ms__c15_v5_sell_atr140(idx: pd.DatetimeIndex) -> np.ndarray:
    """Convert DatetimeIndex to int64 UTC epoch milliseconds."""
    idx_utc = idx.tz_localize('UTC') if idx.tz is None else idx.tz_convert('UTC')
    idx_ms = idx_utc.as_unit('ms')
    return np.asarray(idx_ms.asi8, dtype=np.int64)

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py
def build_c15_1h_atr_ratio_lookup(df_1h: pd.DataFrame, *, atr_period: int=C15_V5_ATR_PERIOD, sma_period: int=C15_V5_ATR_SMA_PERIOD) -> dict[int, float]:
    """Build strictly causal ``{close_ms: ATR(14)_1H / SMA(ATR(14)_1H, 50)}`` from closed 1H bars."""
    if df_1h.empty:
        return {}
    d = df_1h.sort_index()
    atr_14 = atr(d, int(atr_period)).to_numpy(dtype=float)
    atr_sma = pd.Series(atr_14).rolling(int(sma_period), min_periods=int(sma_period)).mean().to_numpy(dtype=float)
    atr_ratio = np.where(np.isfinite(atr_sma) & (atr_sma > 0.0), atr_14 / atr_sma, np.nan)
    open_ms = _index_to_utc_ms__c15_v5_sell_atr140(pd.DatetimeIndex(d.index))
    close_ms = open_ms + H1_MS - 1
    return {int(cms): float(val) for cms, val in zip(close_ms, atr_ratio)}

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py
class C15V5SellAtr140Filter:
    """Locked ETH-USDT/C15 V5_SELL_ONLY_ATR140 candle-only L2 filter."""
    name: str = 'ETH-USDT_C15_V5_SELL_ONLY_ATR140'

    def __init__(self, atr_ratio_by_close_ms: dict[int, float], *, atr_period: int=C15_V5_ATR_PERIOD, sma_period: int=C15_V5_ATR_SMA_PERIOD, atr_ratio_max: float=C15_V5_ATR_RATIO_MAX) -> None:
        self._atr_ratio_by_close_ms = dict(atr_ratio_by_close_ms)
        self.atr_period: int = int(atr_period)
        self.sma_period: int = int(sma_period)
        self.atr_ratio_max: float = float(atr_ratio_max)

    @classmethod
    def from_1h_dataframe(cls, df_1h: pd.DataFrame) -> 'C15V5SellAtr140Filter':
        """Construct filter directly from closed 1H OHLCV bars."""
        lookup = build_c15_1h_atr_ratio_lookup(df_1h, atr_period=C15_V5_ATR_PERIOD, sma_period=C15_V5_ATR_SMA_PERIOD)
        return cls(lookup)

    @classmethod
    def from_m15_dataframe(cls, df_eth_m15: pd.DataFrame | None) -> 'C15V5SellAtr140Filter':
        """Construct filter from closed M15 ETH-USDT OHLCV bars resampled strictly to 1H."""
        if df_eth_m15 is None or df_eth_m15.empty:
            return cls({})
        d1 = resample_ohlcv(ensure_dt(df_eth_m15), '1h')
        if d1.empty:
            return cls({})
        return cls.from_1h_dataframe(d1)

    def config_dict(self) -> dict[str, Any]:
        return {'filter': self.name, 'variant': C15_V5_VARIANT_NAME, 'symbol': C15_SYMBOL, 'combo_id': C15_COMBO_ID, 'timeframe': '1h', 'buy_enabled': False, 'sell_atr_period': self.atr_period, 'sell_atr_sma_period': self.sma_period, 'sell_atr_ratio_max_inclusive': self.atr_ratio_max, 'candle_only': True, 'locked': True}

    def config_hash(self) -> str:
        return hash_filter_config(self.config_dict())

    def evaluate(self, signal: Any, ctx: FilterContext | None=None) -> FilterDecision:
        """Evaluate V5_SELL_ONLY_ATR140 admission strictly from closed 1H candle state."""
        del ctx
        if str(getattr(signal, 'symbol', '')) != C15_SYMBOL or int(getattr(signal, 'combo_id', -1)) != C15_COMBO_ID:
            return FilterDecision(action='KEEP')
        side = str(getattr(signal, 'side', '')).upper()
        if side == 'BUY':
            return FilterDecision(action='DROP', reason='BLOCKED_V5_BUY_DISABLED', label='V5_DROP')
        if side != 'SELL':
            return FilterDecision(action='DROP', reason='BLOCKED_V5_UNKNOWN_SIDE', label='V5_DROP')
        cms = int(getattr(signal, 'signal_close_ms'))
        ratio = self._atr_ratio_by_close_ms.get(cms)
        if ratio is None:
            return FilterDecision(action='DROP', reason='BLOCKED_V5_MISSING_1H_BAR', label='V5_DROP')
        if not math.isfinite(ratio):
            return FilterDecision(action='DROP', reason='BLOCKED_V5_NONFINITE_ATR_RATIO', label='V5_DROP')
        if ratio <= self.atr_ratio_max:
            return FilterDecision(action='KEEP', label='V5_KEEP')
        return FilterDecision(action='DROP', reason='BLOCKED_V5_SELL_ATR_RATIO_GT_140', label='V5_DROP')

# --- from autotrade_v3/filters/c3_b2_not_overextended_4h.py
C3_SYMBOL = "ETH-USDT"

# --- from autotrade_v3/filters/c3_b2_not_overextended_4h.py
C3_COMBO_ID = 3

# --- from autotrade_v3/filters/c3_b2_not_overextended_4h.py
C3_MAX_EMA_TREND_4H_DIR = 3.0

# --- from autotrade_v3/filters/c3_b2_not_overextended_4h.py
H4_MS = 14_400_000

# --- from autotrade_v3/filters/c3_b2_not_overextended_4h.py
def build_c3_4h_trend_lookup(df_eth_m15: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Compute closed-bar 4H ema_trend_4h lookup arrays from M15 OHLCV (zero look-ahead)."""
    d4 = resample_ohlcv(ensure_dt(df_eth_m15), "4h")
    if d4.empty:
        return np.array([], dtype=np.int64), np.array([], dtype=float)
    c4 = d4["close"].astype(float)
    e20 = ema(c4, 20)
    e50 = ema(c4, 50)
    ema_trend_4h = ((e20 - e50) / c4 * 100.0).to_numpy(dtype=float)
    close_ms_4h = (datetime_index_to_ms(d4.index) + H4_MS - 1).astype(np.int64)
    return close_ms_4h, ema_trend_4h

# --- from autotrade_v3/filters/c3_b2_not_overextended_4h.py
class C3B2NotOverextended4hFilter:
    """Block ETH-USDT/C3 entries when directional 4H EMA(20/50) spread exceeds 3.0%."""

    name: str = "C3_B2_NOT_OVEREXTENDED_4H"

    def __init__(
        self,
        close_ms_4h: np.ndarray,
        ema_trend_4h: np.ndarray,
        max_ema_trend_4h_dir: float = C3_MAX_EMA_TREND_4H_DIR,
    ) -> None:
        self.close_ms_4h = close_ms_4h
        self.ema_trend_4h = ema_trend_4h
        self.max_ema_trend_4h_dir = float(max_ema_trend_4h_dir)

    @classmethod
    def from_m15_dataframe(
        cls,
        df_eth_m15: pd.DataFrame,
        max_ema_trend_4h_dir: float = C3_MAX_EMA_TREND_4H_DIR,
    ) -> C3B2NotOverextended4hFilter:
        close_ms_4h, ema_trend_4h = build_c3_4h_trend_lookup(df_eth_m15)
        return cls(close_ms_4h, ema_trend_4h, max_ema_trend_4h_dir=max_ema_trend_4h_dir)

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        return self.evaluate_signal(signal)

    def evaluate_signal(self, sig: Signal) -> FilterDecision:
        if sig.side not in ("BUY", "SELL"):
            return FilterDecision(action="DROP", reason="BLOCKED_C3_UNKNOWN_SIDE", label=self.name)

        sig_ms = int(sig.signal_close_ms)
        idx4 = int(np.searchsorted(self.close_ms_4h, sig_ms, side="right")) - 1
        if idx4 < 0 or np.isnan(self.ema_trend_4h[idx4]):
            return FilterDecision(action="DROP", reason="BLOCKED_C3_WARMUP_4H", label=self.name)

        sgn = 1.0 if sig.side == "BUY" else -1.0
        ema_trend_4h_dir = float(self.ema_trend_4h[idx4]) * sgn
        if ema_trend_4h_dir > self.max_ema_trend_4h_dir:
            return FilterDecision(action="DROP", reason="BLOCKED_C3_OVEREXTENDED_4H", label=self.name)
        return FilterDecision(action="KEEP", reason="PASS", label=self.name)

# --- from autotrade_v3/filters/c6_r2_v4_combined.py
C6_SYMBOL = "ETH-USDT"

# --- from autotrade_v3/filters/c6_r2_v4_combined.py
C6_COMBO_ID = 6

# --- from autotrade_v3/filters/c6_r2_v4_combined.py
C6_BUY_ATR_RATIO_50_MIN = 1.2

# --- from autotrade_v3/filters/c6_r2_v4_combined.py
C6_SELL_ATR_RATIO_50_MIN = 0.9

# --- from autotrade_v3/filters/c6_r2_v4_combined.py
def calc_adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder-smoothed ADX(n) on closed candles."""
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([(high - low), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr_v = tr.ewm(alpha=1.0 / n, adjust=False).mean()
    plus_di = 100.0 * pd.Series(plus_dm, index=high.index).ewm(alpha=1.0 / n, adjust=False).mean() / atr_v.replace(0, np.nan)
    minus_di = 100.0 * pd.Series(minus_dm, index=high.index).ewm(alpha=1.0 / n, adjust=False).mean() / atr_v.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / n, adjust=False).mean()

# --- from autotrade_v3/filters/c6_r2_v4_combined.py
def build_c6_atr_ratio_lookup(df_1h: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Compute closed-bar 1H ATR14 / SMA50(ATR14) lookup arrays."""
    h1 = df_1h["high"].astype(float)
    l1 = df_1h["low"].astype(float)
    c1 = df_1h["close"].astype(float)
    pc1 = c1.shift(1)
    tr1 = pd.concat([(h1 - l1), (h1 - pc1).abs(), (l1 - pc1).abs()], axis=1).max(axis=1)
    atr14_1h = tr1.rolling(14).mean()
    atr_sma50_1h = atr14_1h.rolling(50).mean()
    atr_ratio_50 = (atr14_1h / atr_sma50_1h.replace(0, np.nan)).to_numpy(dtype=float)
    close_ms_1h = (datetime_index_to_ms(df_1h.index) + 3_600_000 - 1).astype(np.int64)
    return close_ms_1h, atr_ratio_50

# --- from autotrade_v3/filters/c6_r2_v4_combined.py
class C6R2V4CombinedFilter:
    """Asymmetric candle-only ATR-ratio filter for ETH-USDT/C6: BUY >= 1.2, SELL >= 0.9."""

    name: str = "C6_R2_V4_COMBINED"

    def __init__(self, close_ms_1h: np.ndarray, atr_ratio_50: np.ndarray) -> None:
        self.close_ms_1h = close_ms_1h
        self.atr_ratio_50 = atr_ratio_50

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        return self.evaluate_signal(signal)

    def evaluate_signal(self, sig: Signal) -> FilterDecision:
        sig_ms = int(sig.signal_close_ms)
        idx = int(np.searchsorted(self.close_ms_1h, sig_ms, side="right")) - 1
        if idx < 0 or np.isnan(self.atr_ratio_50[idx]):
            return FilterDecision(action="DROP", reason="BLOCKED_C6_WARMUP_1H", label=self.name)

        val = float(self.atr_ratio_50[idx])
        if sig.side == "BUY":
            if val < C6_BUY_ATR_RATIO_50_MIN:
                return FilterDecision(action="DROP", reason="BLOCKED_C6_BUY_ATR_RATIO_LT_120", label=self.name)
            return FilterDecision(action="KEEP", reason="PASS", label=self.name)

        if sig.side == "SELL":
            if val < C6_SELL_ATR_RATIO_50_MIN:
                return FilterDecision(action="DROP", reason="BLOCKED_C6_SELL_ATR_RATIO_LT_090", label=self.name)
            return FilterDecision(action="KEEP", reason="PASS", label=self.name)

        return FilterDecision(action="DROP", reason="BLOCKED_C6_UNKNOWN_SIDE", label=self.name)

# --- from autotrade_v3/filters/c6_r2_v4_combined.py
def generate_c6_cci_audit_sell_signals(
    df_15m: pd.DataFrame,
    *,
    combo_name: str,
    tp_pct: float,
    sl_pct: float,
    strategy_id: str,
    exit_policy: Any = None,
    exit_policy_hash: str = "",
    warmup_end_ms: int | None = None,
) -> list[Signal]:
    """Generate corrected CCI-Audit SELL signals for ETH-USDT/C6 on closed 1H candles."""
    d1h = ensure_dt(resample_ohlcv(df_15m, "1h"))
    if d1h.empty:
        return []

    o, h, l, c = d1h["open"], d1h["high"], d1h["low"], d1h["close"]
    R = rsi(c, 14)
    K = stochastic(c, h, l, 14)
    D = K.rolling(3).mean()
    C = cci(c, h, l, 9)
    adx_val = calc_adx(h, l, c, 14)
    adxs = (adx_val > 20) & (adx_val > adx_val.shift(1))
    a21 = atr(d1h, 21)
    std20 = c.rolling(20, min_periods=20).std(ddof=0)
    bbl = sma(c, 20) - 2 * std20
    kl = sma(c, 20) - 1.7 * atr(d1h, 20)
    breakout_s = crossunder(c, bbl) & (c < kl)
    ss = (c < o) & ((o - c) > a21 * 1.8)
    sc6 = (R < 70) & (K < 70) & (K < D) & (C < -100) & (C < C.shift(1))
    edge_sell = edge_event(ss & breakout_s & sc6 & adxs)

    idx_ms = datetime_index_to_ms(d1h.index) + 3_600_000 - 1
    out: list[Signal] = []
    for i, flag in enumerate(edge_sell.values):
        if not flag:
            continue
        ts = int(idx_ms[i])
        if warmup_end_ms is not None and ts < warmup_end_ms:
            continue
        ep = float(c.iloc[i])
        out.append(
            Signal(
                symbol=C6_SYMBOL,
                combo_id=C6_COMBO_ID,
                combo_name=combo_name,
                timeframe="1h",
                side="SELL",
                signal_close_ms=ts,
                signal_close_dt=pd.Timestamp(ts, unit="ms", tz="UTC"),
                bar_position=i,
                bar_open_ms=ts - 3_600_000 + 1,
                entry_price=ep,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                tp_price=ep * (1.0 - tp_pct),
                sl_price=ep * (1.0 + sl_pct),
                approved=True,
                warmup_ready=True,
                smc_dir=0,
                ob_blocked=False,
                provider=strategy_id,
                is_new6=False,
                exit_policy=exit_policy,
                exit_policy_hash=exit_policy_hash,
                signal_id=f"ETH-USDT_C6_SELL_CCI_{ts}",
            )
        )
    return out

# --- from autotrade_v3/filters/c7_r2_v2_asym.py
C7_COMBO_ID = 7

# --- from autotrade_v3/filters/c7_r2_v2_asym.py
C7_BUY_VOL_MULT_MIN = 2.0

# --- from autotrade_v3/filters/c7_r2_v2_asym.py
C7_SELL_EMA_TREND_1H_MAX = 0.0

# --- from autotrade_v3/filters/c7_r2_v2_asym.py
def build_c7_features_lookup(
    df_15m: pd.DataFrame,
    df_1h: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute closed-bar 15m vol_mult and 1h ema_trend_1h lookup arrays."""
    v15 = df_15m["volume"].astype(float)
    vol_mult_15m = (v15 / v15.rolling(20).mean().replace(0, np.nan)).to_numpy(dtype=float)
    close_ms_15m = (datetime_index_to_ms(df_15m.index) + 900_000 - 1).astype(np.int64)

    c1 = df_1h["close"].astype(float)
    ema20_1h = c1.ewm(span=20, adjust=False).mean()
    ema50_1h = c1.ewm(span=50, adjust=False).mean()
    ema_trend_1h = ((ema20_1h - ema50_1h) / c1 * 100.0).to_numpy(dtype=float)
    close_ms_1h = (datetime_index_to_ms(df_1h.index) + 3_600_000 - 1).astype(np.int64)

    return close_ms_15m, vol_mult_15m, close_ms_1h, ema_trend_1h

# --- from autotrade_v3/filters/c7_r2_v2_asym.py
class C7R2V2AsymFilter:
    """Asymmetric candle-only filter for ETH-USDT/C7: BUY vol_mult>=2.0, SELL ema_trend_1h<=0.0."""

    name: str = "C7_R2_V2_ASYM_BUY_VOL20_SELL_TREND"

    def __init__(
        self,
        close_ms_15m: np.ndarray,
        vol_mult_15m: np.ndarray,
        close_ms_1h: np.ndarray,
        ema_trend_1h: np.ndarray,
    ) -> None:
        self.close_ms_15m = close_ms_15m
        self.vol_mult_15m = vol_mult_15m
        self.close_ms_1h = close_ms_1h
        self.ema_trend_1h = ema_trend_1h

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        return self.evaluate_signal(signal)

    def evaluate_signal(self, sig: Signal) -> FilterDecision:
        sig_ms = int(sig.signal_close_ms)
        if sig.side == "BUY":
            idx = int(np.searchsorted(self.close_ms_15m, sig_ms, side="right")) - 1
            if idx < 0 or np.isnan(self.vol_mult_15m[idx]):
                return FilterDecision(action="DROP", reason="BLOCKED_C7_WARMUP_15M", label=self.name)
            if self.vol_mult_15m[idx] < C7_BUY_VOL_MULT_MIN:
                return FilterDecision(action="DROP", reason="BLOCKED_C7_BUY_VOL_LT_20", label=self.name)
            return FilterDecision(action="KEEP", reason="PASS", label=self.name)

        if sig.side == "SELL":
            idx = int(np.searchsorted(self.close_ms_1h, sig_ms, side="right")) - 1
            if idx < 0 or np.isnan(self.ema_trend_1h[idx]):
                return FilterDecision(action="DROP", reason="BLOCKED_C7_WARMUP_1H", label=self.name)
            if self.ema_trend_1h[idx] > C7_SELL_EMA_TREND_1H_MAX:
                return FilterDecision(action="DROP", reason="BLOCKED_C7_SELL_TREND_POS", label=self.name)
            return FilterDecision(action="KEEP", reason="PASS", label=self.name)

        return FilterDecision(action="DROP", reason="BLOCKED_C7_UNKNOWN_SIDE", label=self.name)

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
        """Unique identifier of the strategy (e.g. '20FINAL_BASELINE' or '20FINAL')."""
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
    def calculate_indicators(self, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
        """Calculate indicators for required timeframes without future data lookahead."""
        pass

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
class Baseline20FinalStrategy(BaseStrategy):
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

    def on_bar_close(self, state: Any, bar: Any, only_approved: bool=True, only_warmup_ready: bool=True) -> list[SignalEvent]:
        return self._core.on_bar_close(state=state, bar=bar, only_approved=only_approved, only_warmup_ready=only_warmup_ready)

    @property
    def strategy_id(self) -> str:
        if self._strategy_id_override is not None:
            return self._strategy_id_override
        sp = self._core.spec()
        if sp.strategy_id == '20FINAL' and sp.version == '1':
            return '20FINAL_BASELINE'
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

    def calculate_indicators(self, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
        """Indicator pass-through; indicators are evaluated dynamically during signal replay."""
        return {'status': 'CALCULATED_ONLINE', 'symbols': list(data.keys())}

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
class EthC1H1Both1Strategy(Baseline20FinalStrategy):
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
H1_MS = 3600000

# --- from autotrade_v3/strategy/v3_lab_8.py
H4_MS = 14400000

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
class V3Lab8Strategy(Baseline20FinalStrategy):
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
        if eth_df is None or eth_df.empty:
            return base_signals

        c13_filter = C13B1VolRegimeFilter.from_m15_dataframe(eth_df)

        out: list[Signal] = []
        for sig in base_signals:
            if sig.symbol == C13_SYMBOL and int(sig.combo_id) == C13_COMBO_ID:
                dec = c13_filter.evaluate_signal(sig)
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
