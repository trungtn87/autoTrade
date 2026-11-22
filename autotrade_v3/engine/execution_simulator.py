"""Compatibility facade for ExecutionSimulator delegating to autotrade_v3.sim."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal
import pandas as pd

from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.strategy.base import ExitPolicy


@dataclass
class ExecutionEvent:
    event_id: str
    timestamp_ms: int
    timestamp_dt: str
    trade_id: str
    leg_id: str
    symbol: str
    combo_id: int
    combo_name: str
    timeframe: str
    event_type: Literal["ENTRY", "EXIT"]
    side: Literal["BUY", "SELL"]
    order_action: Literal["OPEN", "CLOSE"]
    price: float
    qty: float
    notional: float
    fee: float
    realized_pnl: float
    exit_reason: str
    signal_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TradeRecord:
    trade_id: str
    leg_id: str
    symbol: str
    combo_id: int
    combo_name: str
    timeframe: str
    side: Literal["BUY", "SELL"]
    leg_type: str  # "SINGLE", "TP_HALF", "TRAIL_HALF"
    entry_time_ms: int
    entry_time_dt: pd.Timestamp
    entry_price: float
    exit_time_ms: int
    exit_time_dt: pd.Timestamp
    exit_price: float
    exit_reason: str
    qty: float
    notional: float
    margin: float
    gross_pnl: float
    fee_total: float
    net_pnl: float
    return_pct: float
    intrabar_ambiguous: bool
    bars_held: int
    provider: str
    signal_id: str = ""
    intrabar_trailing_ambiguous: bool = False
    exit_policy_hash: str = ""
    tp_pct: float = 0.0
    sl_pct: float = 0.0
    tp_price: float = 0.0
    sl_price: float = 0.0
    trailing_act_pct: float = 0.0
    trailing_cb_pct: float = 0.0
    max_bars_held: int | None = None
    entry_px_raw: float = 0.0
    entry_px_fill: float = 0.0
    exit_px_trigger: float = 0.0
    exit_px_fill: float = 0.0
    fee_entry: float = 0.0
    fee_exit: float = 0.0
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    funding_cost: float = 0.0
    gross_pnl_raw: float = 0.0
    ambiguity_type: str = "NONE"
    ambiguity_policy: str = "LEGACY"
    pnl_own_entry: float = 0.0
    pnl_vwap: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["entry_time_dt"] = str(self.entry_time_dt)
        d["exit_time_dt"] = str(self.exit_time_dt)
        return d


@dataclass
class SimulationResult:
    """Encapsulates execution output across research modes (A, B, C)."""

    mode: str
    trades: list[TradeRecord]
    events: list[ExecutionEvent]
    accepted_signals: list[Signal]
    skipped_signals: list[dict[str, Any]]
    rejected_signals: list[dict[str, Any]]
    snapshots: pd.DataFrame
    ambiguity_stats: dict[str, int]
    summary_dict: dict[str, Any] = field(default_factory=dict)
    signal_decisions: list[Any] = field(default_factory=list)
    funding_events: list[Any] = field(default_factory=list)

    @property
    def total_signals_count(self) -> int:
        if self.signal_decisions:
            return len(self.signal_decisions)
        return len(self.accepted_signals) + len(self.skipped_signals) + len(self.rejected_signals)

    @property
    def accepted_trades_count(self) -> int:
        return len({t.trade_id for t in self.trades})

    @property
    def total_legs_count(self) -> int:
        return len(self.trades)


class ExecutionSimulator:
    """Thin compatibility wrapper forwarding all simulation modes to UnifiedSimulator."""

    def __init__(
        self,
        risk_config: RiskConfig | None = None,
        final14_exit_mode: Literal["50TP_50TRAIL", "100_HARD"] = "50TP_50TRAIL",
        trailing_act_pct: float = 0.005,
        trailing_cb_pct: float = 0.005,
        cost_model: Any = None,
        ambiguity_policy: str = "LEGACY",
    ):
        self.risk = risk_config or RiskConfig()
        self.final14_exit_mode = final14_exit_mode
        self.trailing_act_pct = trailing_act_pct
        self.trailing_cb_pct = trailing_cb_pct
        self.cost_model = cost_model if cost_model is not None else getattr(self.risk, "cost_model", None)
        self.ambiguity_policy = ambiguity_policy
        self.execution_events: list[ExecutionEvent] = []
        self.last_skipped_signals: list[dict[str, Any]] = []
        self.last_rejected_signals: list[dict[str, Any]] = []
        self.last_portfolio_summary: dict[str, Any] = {}

    def get_execution_events(self) -> list[ExecutionEvent]:
        return list(self.execution_events)

    def resolve_exit_policy(self, sig: Signal) -> ExitPolicy:
        if getattr(sig, "exit_policy", None) is not None:
            return sig.exit_policy
        use_trailing = (not sig.is_new6) and (self.final14_exit_mode == "50TP_50TRAIL")
        return ExitPolicy(
            tp_pct=sig.tp_pct,
            sl_pct=sig.sl_pct,
            split_legs=use_trailing,
            trailing_act_pct=self.trailing_act_pct,
            trailing_cb_pct=self.trailing_cb_pct,
            leg_weights=(0.5, 0.5) if use_trailing else (1.0,),
        )

    def _unified(self):
        from autotrade_v3.sim.engine import UnifiedSimulator

        return UnifiedSimulator(
            risk_config=self.risk,
            final14_exit_mode=self.final14_exit_mode,
            trailing_act_pct=self.trailing_act_pct,
            trailing_cb_pct=self.trailing_cb_pct,
            cost_model=self.cost_model,
            ambiguity_policy=self.ambiguity_policy,
        )

    def simulate_virtual_trades(
        self,
        signals: list[Signal],
        df_bars: pd.DataFrame,
        allow_concurrency: bool = False,
        clear_skipped: bool = True,
    ) -> list[TradeRecord]:
        if clear_skipped:
            self.last_skipped_signals = []
        if not signals or df_bars.empty:
            return []
        mode = "ALL_SIGNALS" if allow_concurrency else "ONE_TRADE_PER_COMBO"
        candle_dict = {signals[0].symbol: df_bars}
        res = self._unified().run(mode=mode, signals=signals, candle_dict=candle_dict)
        self.execution_events.extend(res.events)
        if clear_skipped:
            self.last_skipped_signals = list(res.skipped_signals)
        else:
            self.last_skipped_signals.extend(res.skipped_signals)
        return res.trades

    def simulate_portfolio_independent(
        self,
        signals: list[Signal],
        candle_dict: dict[str, pd.DataFrame] | pd.DataFrame,
        sample_snapshot_interval_m1: int = 15,
    ) -> tuple[list[TradeRecord], pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
        if isinstance(candle_dict, pd.DataFrame):
            if not signals:
                return [], pd.DataFrame(), [], {}
            candle_dict = {signals[0].symbol: candle_dict}
        if not candle_dict:
            return [], pd.DataFrame(), [], {}
        res = self._unified().run(
            mode="BINGX_PORTFOLIO",
            signals=signals,
            candle_dict=candle_dict,
            sample_snapshot_interval_m1=sample_snapshot_interval_m1,
        )
        self.execution_events = list(res.events)
        self.last_skipped_signals = list(res.skipped_signals)
        self.last_rejected_signals = list(res.rejected_signals)
        self.last_portfolio_summary = dict(res.summary_dict)
        return res.trades, res.snapshots, res.rejected_signals, res.summary_dict

    def simulate_portfolio_trades(
        self,
        signals: list[Signal],
        df_bars: pd.DataFrame | dict[str, pd.DataFrame],
    ) -> tuple[list[TradeRecord], pd.DataFrame]:
        closed_trades, df_snapshots, _, _ = self.simulate_portfolio_independent(signals, df_bars)
        return closed_trades, df_snapshots

    def simulate_research_mode(
        self,
        mode: Literal["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"] | str,
        signals: list[Signal],
        candle_dict: dict[str, pd.DataFrame],
        pre_filtered_decisions: list[Any] | None = None,
    ) -> SimulationResult:
        res = self._unified().run(
            mode=mode,
            signals=signals,
            candle_dict=candle_dict,
            pre_filtered_decisions=pre_filtered_decisions,
        )
        self.execution_events = list(res.events)
        self.last_skipped_signals = list(res.skipped_signals)
        self.last_rejected_signals = list(res.rejected_signals)
        return res
