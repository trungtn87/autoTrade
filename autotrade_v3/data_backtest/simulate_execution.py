"""V3-03 local-only M1 execution *scenarios*, not exchange verified fills.

Uses fixed, deterministic two-path M1 assumptions (O-H-L-C and O-L-H-C).
These are scenario outcomes, NOT mathematical upper/lower bounds for arbitrary
intraminute price paths or BingX native trigger/order sequencing.

No network, credentials, production executor imports or live DB writes.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import importlib.util
import json
import math
import subprocess
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

SYMBOLS = ("BTC-USDT", "ETH-USDT")
M1_MS = 60_000
FUNDING_PERIOD_MS = 8 * 60 * 60_000
EXIT_MODES = ("split_trailing", "hard_tp")
PATHS = ("HIGH_FIRST", "LOW_FIRST")


@dataclass(frozen=True)
class Profile:
    exit_mode: str = "split_trailing"
    notional_usdt: float = 100.0
    leverage: float = 50.0
    quantity_precision: int = 4
    fee_bps_per_side: float = 5.0
    entry_slippage_bps: float = 2.0
    exit_slippage_bps: float = 2.0
    half_spread_bps: float = 1.0
    entry_latency_ms: int = 0
    trailing_activation_pct: float = 0.005
    trailing_callback_rate: float = 0.005
    funding_rate_per_8h: float = 0.0
    max_holding_minutes: int = 0  # 0 = unlimited; if set, censored, never fabricate exit

    def __post_init__(self):
        if self.exit_mode not in EXIT_MODES:
            raise ValueError("unsupported exit mode")
        if (not math.isfinite(self.notional_usdt) or self.notional_usdt <= 0
                or not math.isfinite(self.leverage) or self.leverage < 1):
            raise ValueError("finite positive notional and leverage >= 1 required")
        if not (0 <= self.quantity_precision <= 12):
            raise ValueError("quantity precision must be 0..12")
        for f in ("fee_bps_per_side", "entry_slippage_bps", "exit_slippage_bps",
                  "half_spread_bps", "funding_rate_per_8h"):
            x = getattr(self, f)
            if (not math.isfinite(x) or
                    (f != "funding_rate_per_8h" and not 0 <= x < 10000) or
                    (f == "funding_rate_per_8h" and abs(x) >= 1)):
                raise ValueError(f"invalid {f}")
        if not (0 < self.trailing_activation_pct < 1 and
                0 < self.trailing_callback_rate < 1):
            raise ValueError("invalid trailing parameters")
        if self.entry_latency_ms < 0 or self.max_holding_minutes < 0:
            raise ValueError("latency / max holding must be nonnegative")


@dataclass(frozen=True)
class Candle:
    t: int
    open: float
    high: float
    low: float
    close: float
    provider: str = "UNKNOWN"

    def __post_init__(self):
        x = (self.open, self.high, self.low, self.close)
        if (self.provider.upper() not in ('BINGX','BINANCE')
                or self.t % M1_MS or not all(math.isfinite(v) and v > 0 for v in x)
                or self.low > min(self.open, self.close) or
                self.high < max(self.open, self.close)):
            raise ValueError("invalid M1 candle/time")


@dataclass
class Leg:
    name: str
    qty: float
    open: bool = True
    active: bool = False
    best: float = 0.0
    trail_stop: float = 0.0
    exit_price: float | None = None
    exit_t: int | None = None
    exit_reason: str | None = None


@dataclass
class TradeScenario:
    status: str
    path: str
    signal_close_ms: int
    entry_t: int | None = None
    entry_price: float | None = None
    qty: float = 0.0
    net_usdt: float | None = None
    realized_net_usdt: float = 0.0
    gross_usdt: float = 0.0
    fees_usdt: float = 0.0
    funding_usdt: float = 0.0
    unrealized_usdt_est: float | None = None
    exit_t: int | None = None
    legs: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    provider_switches: int = 0
    volume_constraint: str = "NOT_MODELED"
    estimated_roi_on_margin_pct: float | None = None
    estimated_roi_on_notional_pct: float | None = None


def _quantize_qty(x: float, precision: int) -> float:
    q = Decimal('1').scaleb(-precision)
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_DOWN))


def _adverse(px: float, entry: bool, long: bool, p: Profile) -> float:
    bps = (p.entry_slippage_bps if entry else p.exit_slippage_bps) + p.half_spread_bps
    # Buy costs more, sell receives less, for both long entry and short exit.
    is_buy = long if entry else not long
    return px * (1 + bps / 10000 if is_buy else 1 - bps / 10000)


def _leg_plan(qty: float, p: Profile) -> tuple[list[Leg], str, list[str]]:
    flags = []
    if p.exit_mode == 'hard_tp':
        return [Leg('TP', qty)], 'hard_tp', flags
    half = _quantize_qty(qty / 2, p.quantity_precision)
    trailing = round(qty - half, p.quantity_precision)
    if half <= 0 or trailing <= 0:
        flags.append('SPLIT_FALLBACK_QTY_PRECISION_UNVERIFIED_EXCHANGE_RULES')
        return [Leg('TP', qty)], 'hard_tp', flags
    return [Leg('TP', half), Leg('TRAIL', trailing)], 'split_trailing', flags


def _stop_price(sl: float, leg: Leg, long: bool) -> tuple[float, str]:
    if leg.name != 'TRAIL' or not leg.active:
        return sl, 'SL'
    if long:
        stop = max(sl, leg.trail_stop)
        return stop, ('TRAILING' if stop > sl else 'SL')
    stop = min(sl, leg.trail_stop)
    return stop, ('TRAILING' if stop < sl else 'SL')


def _path_step(a: float, b: float, long: bool, legs: list[Leg], sl: float,
               tp: float, activation: float, callback: float, close_leg) -> None:
    """Process a monotone subpath; immediate trailing update at subpath extrema.

    This deliberately assumes each minute traverses O-H-L-C or O-L-H-C once.
    Native mark-price / millisecond tick sequence cannot be inferred from last M1.
    """
    if a == b:
        return
    favorable = (b > a) if long else (b < a)
    if favorable:
        for leg in legs:
            if not leg.open:
                continue
            if leg.name == 'TP':
                if (a <= tp <= b) if long else (b <= tp <= a):
                    close_leg(leg, tp, 'TP')
            else:
                if not leg.active and ((b >= activation) if long else (b <= activation)):
                    leg.active = True
                    leg.best = b
                elif leg.active:
                    leg.best = max(leg.best, b) if long else min(leg.best, b)
                if leg.active:
                    leg.trail_stop = leg.best * (1 - callback if long else 1 + callback)
    else:
        for leg in legs:
            if not leg.open:
                continue
            stop, reason = _stop_price(sl, leg, long)
            if ((b <= stop <= a) if long else (a <= stop <= b)):
                close_leg(leg, stop, reason)


def _open_gap(spot: float, long: bool, legs: list[Leg], sl: float,
              tp: float, close_leg) -> bool:
    """Market conditional orders triggered at bar open. Stop gap uses spot, not stop."""
    found = False
    for leg in legs:
        if not leg.open:
            continue
        stop, reason = _stop_price(sl, leg, long)
        if (spot <= stop) if long else (spot >= stop):
            close_leg(leg, spot, reason + '_GAP')
            found = True
        elif leg.name == 'TP' and ((spot >= tp) if long else (spot <= tp)):
            close_leg(leg, spot, 'TP_GAP')
            found = True
    return found


def simulate_one(event: dict, candles: list[Candle], times: list[int],
                 p: Profile, tp_pct: float, sl_pct: float, path: str) -> TradeScenario:
    if path not in PATHS:
        raise ValueError("unsupported intraminute path")
    signal_t = int(event['signal_close_ms'])
    ret = TradeScenario(status='NO_ENTRY', path=path, signal_close_ms=signal_t)
    target = signal_t + 1 + p.entry_latency_ms
    i0 = bisect.bisect_left(times, target)
    if i0 >= len(candles):
        ret.flags.append('NO_FUTURE_M1_FOR_ENTRY')
        return ret
    initial = candles[i0]
    if initial.t != ((target + M1_MS - 1) // M1_MS) * M1_MS:
        raise ValueError('M1 data gap before entry: no silent forward fill')
    long = event['side'].upper() == 'BUY'
    entry = _adverse(initial.open, True, long, p)
    if entry <= 0:
        raise ValueError('invalid adverse entry price')
    qty = _quantize_qty(p.notional_usdt / entry, p.quantity_precision)
    if qty <= 0:
        ret.flags.append('QTY_ROUNDED_TO_ZERO')
        return ret
    legs, used_mode, flags = _leg_plan(qty, p)
    ret.flags.extend(flags)
    ret.entry_price = entry
    ret.entry_t = initial.t
    ret.qty = qty
    ret.status = 'OPEN_END'
    ret.flags.append('ASSUMED_EXIT_MODE_' + used_mode.upper())
    entry_fees = qty * entry * p.fee_bps_per_side / 10000
    ret.fees_usdt = entry_fees
    tp = entry * (1 + tp_pct if long else 1 - tp_pct)
    sl = entry * (1 - sl_pct if long else 1 + sl_pct)
    activation = entry * (1 + p.trailing_activation_pct if long
                          else 1 - p.trailing_activation_pct)
    liquidation_approx = entry * (1 - 1/p.leverage if long else 1 + 1/p.leverage)
    realized = 0.0
    fees_exit = 0.0
    closure_t = None
    current_t = initial.t

    def close_leg(leg: Leg, trigger: float, reason: str):
        nonlocal realized, fees_exit, closure_t
        if not leg.open:
            return
        fill = _adverse(trigger, False, long, p)
        leg.open = False
        leg.exit_price = fill
        leg.exit_t = current_t
        leg.exit_reason = reason
        realized += ((fill - entry) if long else (entry - fill)) * leg.qty
        fees_exit += fill * leg.qty * p.fee_bps_per_side / 10000
        closure_t = current_t

    # Entry and native protection placement are not simultaneous in live trading.
    # Do not assume a fill at TP/SL on the same entry M1: flag and delay checks.
    if ((initial.low <= sl or initial.high >= tp) if long else
            (initial.high >= sl or initial.low <= tp)):
        ret.flags.append('ENTRY_MINUTE_TP_SL_ORDERING_UNOBSERVABLE')
    if used_mode == 'split_trailing' and (
            (initial.high >= activation) if long else (initial.low <= activation)):
        ret.flags.append('ENTRY_MINUTE_TRAILING_ACTIVATION_UNOBSERVABLE')
    last_provider = initial.provider
    seen_liquidation = False
    prev_t = initial.t
    for i in range(i0 + 1, len(candles)):
        c = candles[i]
        if c.t - prev_t != M1_MS:
            raise ValueError('noncontinuous M1 during open exposure')
        prev_t = c.t
        current_t = c.t
        if p.max_holding_minutes and i - i0 >= p.max_holding_minutes:
            ret.flags.append('MAX_HOLDING_CENSORED_NOT_FORCED_EXIT')
            break
        if c.provider != last_provider:
            ret.provider_switches += 1
            last_provider = c.provider
            ret.flags.append('CROSS_VENUE_HISTORY')
        if not seen_liquidation and ((c.low <= liquidation_approx) if long else
                                     (c.high >= liquidation_approx)):
            seen_liquidation = True
            ret.flags.append('APPROX_LIQUIDATION_THRESHOLD_WICK_MARK_BASIS_UNKNOWN')
        outstanding = sum(x.qty for x in legs if x.open)
        if outstanding > 0 and c.t % FUNDING_PERIOD_MS == 0 and p.funding_rate_per_8h:
            # Signed *assumed* funding, not exchange mark funding evidence.
            ret.funding_usdt += -p.funding_rate_per_8h * c.open * outstanding * (1 if long else -1)
        _open_gap(c.open, long, legs, sl, tp, close_leg)
        if all(not x.open for x in legs):
            break
        path_prices = (c.open, c.high, c.low, c.close) if path == 'HIGH_FIRST' else (
            c.open, c.low, c.high, c.close)
        for start, end in zip(path_prices, path_prices[1:]):
            _path_step(start, end, long, legs, sl, tp, activation,
                       p.trailing_callback_rate, close_leg)
            if all(not leg.open for leg in legs):
                break
        if all(not leg.open for leg in legs):
            break
    ret.gross_usdt = realized
    ret.fees_usdt += fees_exit
    ret.realized_net_usdt = realized - ret.fees_usdt + ret.funding_usdt
    if all(not x.open for x in legs):
        ret.status = 'CLOSED_SIMULATED'
        ret.exit_t = closure_t
        ret.net_usdt = ret.realized_net_usdt
        ret.estimated_roi_on_margin_pct = 100 * ret.net_usdt / (qty * entry / p.leverage)
        ret.estimated_roi_on_notional_pct = 100 * ret.net_usdt / (qty * entry)
    else:
        ret.status = 'OPEN_END_OR_CENSORED'
        ret.net_usdt = None  # no false ROI on an unresolved / open position
        last = candles[min(len(candles)-1, i0 + p.max_holding_minutes)
                       if p.max_holding_minutes else -1]
        ret.unrealized_usdt_est = sum(
            (((last.close - entry) if long else (entry - last.close)) * x.qty)
            for x in legs if x.open)
    ret.legs = [dict(name=l.name, qty=l.qty, exit_price=l.exit_price,
                     exit_t=l.exit_t, exit_reason=l.exit_reason, open=l.open)
                for l in legs]
    return ret


def simulate_event(event: dict, candles: list[Candle], times: list[int], p: Profile,
                   tp_pct: float, sl_pct: float) -> dict:
    scenarios = {name: simulate_one(event, candles, times, p, tp_pct, sl_pct, name)
                 for name in PATHS}
    a, b = scenarios.values()
    both_closed = all(x.status == 'CLOSED_SIMULATED' for x in scenarios.values())
    disagree = (a.status != b.status or a.exit_t != b.exit_t or
                [x['exit_reason'] for x in a.legs] != [x['exit_reason'] for x in b.legs]
                or (both_closed and abs(a.net_usdt - b.net_usdt) > 1e-8))
    approx = sorted(x.net_usdt for x in scenarios.values() if x.net_usdt is not None)
    flags = sorted(set(a.flags + b.flags + (['M1_PATH_AMBIGUOUS'] if disagree else [])))
    intrinsic_unknown = any(x.startswith(('ENTRY_MINUTE_TP_SL_', 'ENTRY_MINUTE_TRAILING_',
                       'APPROX_LIQUIDATION_', 'SPLIT_FALLBACK_')) for x in flags)
    common = dict(symbol=event['symbol'], combo=event['combo'], combo_id=int(event['combo_id']),
                  side=event['side'], signal_close_ms=int(event['signal_close_ms']),
                  scenario_quality=('AMBIGUOUS' if disagree or intrinsic_unknown else 'SIMULATED'),
                  intraminute_paths_disagree=disagree,
                  scenario_net_min_usdt=approx[0] if both_closed else None,
                  scenario_net_max_usdt=approx[-1] if both_closed else None,
                  closed_in_both=both_closed, flags=flags,
                  shared_container_overlap=False,
                  high_first=asdict(a), low_first=asdict(b),
                  evidence_level='SIMULATED_NOT_BINGX_VERIFIED',
                  trigger_basis='M1_LAST_PROXY_NOT_NATIVE_MARK_OR_TICK')
    return common


def load_events(path: Path) -> tuple[dict[str, list[dict]], dict]:
    events = defaultdict(list)
    counts = Counter()
    seen = set()
    with path.open('r', newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        required = {'symbol', 'combo_id', 'combo', 'side', 'signal_close_ms', 'approved', 'warmup_ready'}
        if not required.issubset(reader.fieldnames or ()):  # no custom signal fabrications
            raise ValueError('input is not a V3-02 replay signal_events.csv')
        for row in reader:
            counts['raw_input'] += 1
            if row['approved'].lower() not in ('true', '1'):
                counts['filter_rejected'] += 1
                continue
            counts['approved'] += 1
            if row['warmup_ready'].lower() not in ('true', '1'):
                counts['warmup_only'] += 1
                continue
            counts['eligible'] += 1
            if row['symbol'] not in SYMBOLS or row['side'] not in ('BUY', 'SELL'):
                raise ValueError('unexpected symbol/side in V3-02 output')
            key = (row['symbol'], row['combo_id'], row['side'], row['signal_close_ms'])
            if key in seen:
                raise ValueError('duplicate approved V3-02 event identity')
            seen.add(key)
            events[row['symbol']].append(row)
    for group in events.values():
        group.sort(key=lambda x: (int(x['signal_close_ms']), int(x['combo_id']), x['side']))
    return dict(events), dict(counts)


def _config_map() -> dict[tuple[str, int], dict]:
    """Import ONLY pinned V2 pure config file by path, never executor/engine."""
    file = Path(__file__).resolve().parents[2] / 'autotrade_v2/app/strategy/final14_config.py'
    spec = importlib.util.spec_from_file_location('v3_readonly_locked_config', file)
    if not spec or not spec.loader:
        raise RuntimeError('V2 pure config unavailable')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {(symbol, combo): cfg for source in (mod.FINAL14_CASES, mod.NEW6_CASES)
            for symbol, items in source.items() for combo, cfg in items.items()}


def _read_candles(catalog: Path, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
    import duckdb
    con = duckdb.connect(str(catalog), read_only=True)
    try:
        rows = con.execute('''SELECT open_ms,open,high,low,"close",provider
            FROM v3_m1 WHERE symbol=? AND open_ms >= ? AND open_ms < ?
            ORDER BY open_ms''', [symbol.replace('-', ''), start_ms, end_ms]).fetchall()
    finally:
        con.close()
    candles = [Candle(int(t), float(o), float(h), float(l), float(c), str(provider))
               for t,o,h,l,c,provider in rows]
    for previous, current in zip(candles, candles[1:]):
        if current.t - previous.t != M1_MS:
            raise ValueError('M1 catalog includes missing/duplicate minutes')
    return candles


def _mark_merged(trades: list[dict]) -> dict:
    """Efficient overlap sweep; exchange position allocation is *not* inferred.

    Hedge mode permits same-symbol/same-side combo concurrency. A union window
    for both scenarios is intentionally cautious; unresolved positions remain
    active until outside the simulated horizon, never silently considered flat.
    """
    by = defaultdict(list)
    for trade in trades:
        starts = [x['entry_t'] for x in (trade['high_first'], trade['low_first'])
                  if x['entry_t'] is not None]
        if not starts:
            continue
        ends = [x['exit_t'] for x in (trade['high_first'], trade['low_first'])
                if x['exit_t'] is not None]
        lo = min(starts)
        end = max(ends) if trade['closed_in_both'] else 2**62
        by[(trade['symbol'], trade['side'])].append((lo, end, trade))
    pairs, max_concurrent = 0, 0
    for members in by.values():
        members.sort(key=lambda x: x[0])
        active = []
        for start, end, trade in members:
            active = [x for x in active if x[1] >= start]
            for prev_start, prev_end, other in active:
                if other['combo_id'] != trade['combo_id']:
                    trade['shared_container_overlap'] = True
                    other['shared_container_overlap'] = True
                    pairs += 1
            active.append((start, end, trade))
            max_concurrent = max(max_concurrent, len(active))
    return dict(shared_container_pairs=pairs, max_concurrent_same_side=max_concurrent,
                trades_touching_shared_container=sum(t['shared_container_overlap'] for t in trades),
                note='VIRTUAL SAME-SIDE OVERLAP IS NOT VERIFIED BINGX POSITION ALLOCATION')


def run(events_csv: Path, catalog: Path, out: Path, profile: Profile,
        start: str='2023-11-01', end: str='2026-11-01') -> dict:
    def utc_ms(s):
        return int(datetime.strptime(s, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000)
    start_ms, end_ms = utc_ms(start), utc_ms(end)
    if not start_ms < end_ms:
        raise ValueError('bad time range')
    events, counts = load_events(events_csv)
    configs = _config_map()
    results = []
    conflicts = Counter()
    total_candles = 0
    for symbol in SYMBOLS:
        candles = _read_candles(catalog, symbol, start_ms, end_ms)
        total_candles += len(candles)
        times = [c.t for c in candles]
        busy_until = {}  # independent *virtual* combo ledger; same-side peers allowed
        for ev in events.get(symbol, []):
            ts = int(ev['signal_close_ms'])
            combo = int(ev['combo_id'])
            key = (symbol, combo)
            if key not in configs:
                raise ValueError(f'unknown locked combo {key}')
            if not start_ms <= ts < end_ms:
                conflicts['signal_outside_window'] += 1
                continue
            if ts <= busy_until.get(key, -1):
                conflicts['same_combo_busy_skipped'] += 1
                continue
            cfg = configs[key]
            result = simulate_event(ev, candles, times, profile,
                                    float(cfg['tp_pct']), float(cfg['sl_pct']))
            results.append(result)
            if (result['high_first']['entry_t'] is None and
                    result['low_first']['entry_t'] is None):
                conflicts['entry_not_fillable'] += 1
                continue
            ends = [x['exit_t'] for x in (result['high_first'], result['low_first'])]
            if not result['closed_in_both']:
                busy_until[key] = 2**62  # no fabricated free combo before confirmed close
            else:
                busy_until[key] = max(int(x) for x in ends)
    merge = _mark_merged(results)
    complete = [x for x in results if x['closed_in_both']]
    ambiguous = sum(x['scenario_quality']=='AMBIGUOUS' for x in results)
    diverging_paths = sum(x['intraminute_paths_disagree'] for x in results)
    by_symbol_combo = Counter((x['symbol'],x['combo']) for x in results)
    # Scenario sums are estimates, not exchange-verified account PnL / ROI.
    combined = [x for x in complete if not x['shared_container_overlap']]
    summed = dict(
        completed_both_scenarios=len(complete),
        non_overlapping_completed=len(combined),
        net_min_usdt_scenarios_sum=round(sum(x['scenario_net_min_usdt'] for x in complete), 6),
        net_max_usdt_scenarios_sum=round(sum(x['scenario_net_max_usdt'] for x in complete), 6),
        net_min_usdt_nonoverlap_sum=round(sum(x['scenario_net_min_usdt'] for x in combined), 6),
        net_max_usdt_nonoverlap_sum=round(sum(x['scenario_net_max_usdt'] for x in combined), 6),
    )
    out.mkdir(parents=True, exist_ok=True)
    jsonl = out/'scenario_trades.jsonl'
    with jsonl.open('w', encoding='utf-8') as f:
        for x in results:
            f.write(json.dumps(x, ensure_ascii=False, sort_keys=True)+'\n')
    profile_hash = hashlib.sha256(json.dumps(asdict(profile),sort_keys=True).encode()).hexdigest()
    source_sha = hashlib.sha256(events_csv.read_bytes()).hexdigest()
    file = Path(__file__)
    config_file = file.resolve().parents[2]/'autotrade_v2/app/strategy/final14_config.py'
    try:
        git_sha = subprocess.check_output(['git','rev-parse','HEAD'],cwd=file.resolve().parents[2],text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError):
        git_sha = 'NOT_AVAILABLE'
    report = dict(
        gate='C1', status='RESEARCH_SCENARIOS_NOT_BINGX_CALIBRATED',
        source_events_sha256=source_sha, source_catalog=str(catalog),
        config_sha256=hashlib.sha256(config_file.read_bytes()).hexdigest(),
        simulator_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
        profile_sha256=profile_hash, source_git_sha=git_sha,
        profile=asdict(profile), window_utc=[start, end],
        input_counts=counts, simulated_candles_total=total_candles,
        events_skipped=dict(conflicts), total_entries_simulated=len(results),
        ambiguous_scenario_count=ambiguous, intraminute_path_disagreement_count=diverging_paths,
        stopped_open_or_unresolved=len(results)-len(complete),
        by_symbol_combo=[dict(symbol=s,combo=c,simulated_entries=n)
                         for (s,c),n in sorted(by_symbol_combo.items())],
        combined_scenario_estimates=summed,
        shared_position_risk=merge,
        unmodeled=['native BingX mark price or tick order', 'order book liquidity and partial fills',
                   'historical exit_mode evidence', 'native order acknowledgment and downtime',
                   'precise funding rates, margin maintenance and liquidation',
                   'BingX combo-level PnL allocation in merged hedge containers'],
        caveat='Two deterministic path outcomes are NOT guaranteed mathematical bounds. '
               'No verified net PnL, win rate, portfolio ROI or exchange match. '
               'Calibration and Gate C2 require V2 export + BingX fills.',
    )
    (out/'execution_report.json').write_text(json.dumps(report,indent=2,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf-8')
    return report


def main() -> int:
    parser=argparse.ArgumentParser(description='V3-03 offline M1 execution scenarios')
    parser.add_argument('--events',type=Path,required=True)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--start',default='2023-11-01')
    parser.add_argument('--end',default='2026-11-01')
    parser.add_argument('--exit-mode',choices=EXIT_MODES,default='split_trailing')
    parser.add_argument('--fee-bps',type=float,default=5)
    parser.add_argument('--entry-slippage-bps',type=float,default=2)
    parser.add_argument('--exit-slippage-bps',type=float,default=2)
    parser.add_argument('--half-spread-bps',type=float,default=1)
    parser.add_argument('--entry-latency-ms',type=int,default=0)
    parser.add_argument('--funding-rate-per-8h',type=float,default=0.0)
    parser.add_argument('--max-holding-minutes',type=int,default=0)
    args=parser.parse_args()
    p=Profile(exit_mode=args.exit_mode,fee_bps_per_side=args.fee_bps,
              entry_slippage_bps=args.entry_slippage_bps,
              exit_slippage_bps=args.exit_slippage_bps,
              half_spread_bps=args.half_spread_bps,
              entry_latency_ms=args.entry_latency_ms,
              funding_rate_per_8h=args.funding_rate_per_8h,
              max_holding_minutes=args.max_holding_minutes)
    try:
        report=run(args.events,args.catalog,args.out,p,args.start,args.end)
    except (OSError,ValueError,RuntimeError,KeyError) as exc:
        parser.exit(2,f'V3-03 error: {exc}\n')
    print(json.dumps({k:report[k] for k in ('gate','status','total_entries_simulated',
              'ambiguous_scenario_count','intraminute_path_disagreement_count',
              'stopped_open_or_unresolved')},sort_keys=True))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
