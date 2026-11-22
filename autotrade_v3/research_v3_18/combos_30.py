"""Dựng lại và liệt kê chính xác ĐỦ 30 mã/combo của đợt chạy chéo 30 (AUDIT_20_POSITIVE_FIXED_TPSL_20261116)."""
from __future__ import annotations

from collections.abc import Collection
import copy
from dataclasses import asdict, dataclass
import dataclasses
import json
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.strategy.base import ExitPolicy
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.strategy.eth_c1_h1_both_1 import (
    H1_BUY_MAX_SLOPE_4H_PCT,
    H1_SELL_MIN_DIST_EMA150_4H_PCT,
    EthC1H1Both1Strategy,
)
from autotrade_v3.strategy.v3_lab_11_prune23 import PRUNE23_EXCLUDED_COMBOS
from autotrade_v3.strategy_core.api import ComboSpec, SignalEvent, Strategy, StrategySpec
from autotrade_v3.strategy_core.hashing import canonical_json_sha256, compute_spec_hash
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry

CROSS30_SOURCE_COMMIT = "5c471903090f174c349f062c0de8b555cadd2895"
CROSS30_RUN_SOURCE_COMMIT = "51234e35a145e29cf4a5cd3fc3fb5e69a6366c55"
CROSS30_PR124_ANCESTOR = "c99e4529ac1761614ccfd1fdbed8a92315e7a5d7"
LIVE23_SOURCE_COMMIT = "166aafd"
LIVE23_REGISTRY_REF = "V3_LAB_11_PRUNE23@11.0.0"

# 20 mã dương được xác nhận trong AUDIT_20_POSITIVE_FIXED_TPSL_20261116
AUDIT_20_POSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "ETH-USDT/C4",
        "BTC-USDT/C11",
        "BTC-USDT/C101",  # alias C11
        "BTC-USDT/C6",
        "ETH-USDT/C7",
        "BTC-USDT/C4",
        "ETH-USDT/C13",
        "ETH-USDT/C3",
        "BTC-USDT/C1",
        "BTC-USDT/C2",
        "ETH-USDT/C15",
        "BTC-USDT/C7",
        "BTC-USDT/C16",
        "ETH-USDT/C1",
        "ETH-USDT/C11",
        "ETH-USDT/C10",
        "ETH-USDT/C12",
        "ETH-USDT/C6",
        "BTC-USDT/C10",
        "BTC-USDT/C9",
    }
)


@dataclass(frozen=True)
class ComboCatalogRow:
    """Bản ghi mô tả đầy đủ 1 mã trong bộ 30 mã đợt chạy chéo 30."""

    combo_key: str
    combo_symbol: str
    symbol: str
    combo_id: int
    combo_name: str
    timeframe: str
    directions_cross30: str
    entry_variant: str
    entry_params_json: str
    layer2: str
    tp_pct: float
    sl_pct: float
    origin_type: str
    cloned_from_symbol: str | None
    source_commit_cross30: str
    is_live_23: bool
    live_status_vi: str
    live_source_commit: str
    audit_20_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_cross_symbol_30_core() -> Strategy:
    """Dựng core 30 mã từ 20FINAL@1 và nhân bản đúng 10 cặp thiếu sang BTC/ETH."""
    base_core = StrategyCoreRegistry.get("20FINAL", "1")
    base_spec = base_core.spec()

    combos: list[ComboSpec] = [copy.deepcopy(c) for c in base_spec.combos]
    existing_pairs = {(c.combo_id, c.symbol) for c in combos}

    by_id: dict[int, list[ComboSpec]] = {}
    for c in combos:
        by_id.setdefault(int(c.combo_id), []).append(c)

    for cid in sorted(by_id.keys()):
        items = by_id[cid]
        if len(items) == 1:
            src = items[0]
            target_sym = "ETH-USDT" if src.symbol == "BTC-USDT" else "BTC-USDT"
            if (cid, target_sym) not in existing_pairs:
                new_key = f"{target_sym}/{src.combo_name}"
                cloned = dataclasses.replace(
                    copy.deepcopy(src),
                    symbol=target_sym,
                    combo_key=new_key,
                )
                combos.append(cloned)

    if len(combos) != 30:
        raise RuntimeError(f"Kỳ vọng đủ 30 mã, thực tế dựng được {len(combos)} mã")

    class _CrossSymbol30Core(Strategy):
        def __init__(self, inner: Strategy, new_combos: list[ComboSpec]) -> None:
            inner_copy: Any = copy.deepcopy(inner)
            self._inner: Any = inner_copy
            self._spec = dataclasses.replace(
                inner_copy.spec(),
                strategy_id="20FINAL_CROSS_SYMBOL_30",
                combos=tuple(new_combos),
            )
            setattr(inner_copy, "_spec", self._spec)

        def spec(self) -> StrategySpec:
            return self._spec

        def generate(
            self,
            bars: dict[str, pd.DataFrame] | pd.DataFrame,
            symbols: list[str] | None = None,
            only_approved: bool = True,
            only_warmup_ready: bool = True,
        ) -> list[SignalEvent]:
            return list(
                self._inner.generate(
                    bars=bars,
                    symbols=symbols,
                    only_approved=only_approved,
                    only_warmup_ready=only_warmup_ready,
                )
            )

        def on_bar_close(
            self,
            state: Any,
            bar: Any,
            only_approved: bool = True,
            only_warmup_ready: bool = True,
        ) -> list[SignalEvent]:
            return list(
                self._inner.on_bar_close(
                    state=state,
                    bar=bar,
                    only_approved=only_approved,
                    only_warmup_ready=only_warmup_ready,
                )
            )

    return _CrossSymbol30Core(base_core, combos)


class CrossSymbol30FixedStrategy(Baseline20FinalStrategy):
    """Tái lập chính xác 30 mã của đợt chạy chéo 30 (`run_20final_cross_symbol_30.py`).

    - 28 mã (`combo_id != 1`): dùng logic `20FINAL_CROSS_SYMBOL_30` + Fixed TP/SL (`split_legs=False`).
    - `BTC-USDT/C1` (`combo_id=1`): giữ nguyên `MFI_L50_S50` + `SMC_STRICT` + Fixed TP 2.0% / SL 0.8%.
    - `ETH-USDT/C1` (`combo_id=1`): dùng `H1_BOTH_1` (`EthC1H1Both1Strategy`) + `CCI < -80` + Fixed TP 2.0% / SL 0.666667%.
    """

    def __init__(self) -> None:
        core = build_cross_symbol_30_core()
        super().__init__(
            core_strategy=core,
            strategy_id_override="20FINAL_CROSS_SYMBOL_30",
            version_override="1.0.0",
        )
        self._eth_c1_h1 = EthC1H1Both1Strategy(
            enable_eth_c1_h1_l2=True,
            buy_max_slope_4h_pct=H1_BUY_MAX_SLOPE_4H_PCT,
            sell_min_dist_ema150_4h_pct=H1_SELL_MIN_DIST_EMA150_4H_PCT,
            pre_edge_h1=True,
        )

    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy:
        if symbol == "ETH-USDT" and int(combo_id) == 1:
            return self._eth_c1_h1.get_exit_policy(symbol, combo_id)
        ep = super().get_exit_policy(symbol, combo_id)
        return ExitPolicy(
            tp_pct=float(ep.tp_pct),
            sl_pct=float(ep.sl_pct),
            split_legs=False,
            trailing_act_pct=0.0,
            trailing_cb_pct=0.0,
            leg_weights=(1.0,),
            max_bars_held=ep.max_bars_held,
            force_close_on_window_end=ep.force_close_on_window_end,
        )

    def get_signal_spec_hash(self) -> str:
        return canonical_json_sha256(
            {
                "strategy_id": self.strategy_id,
                "version": self.version,
                "combos": [c.to_dict() for c in sorted(self.spec().combos, key=lambda x: x.combo_key)],
                "eth_c1_override": f"H1_BOTH_1({H1_BUY_MAX_SLOPE_4H_PCT},{H1_SELL_MIN_DIST_EMA150_4H_PCT})+CCI<-80",
            }
        )

    def get_exit_spec_hash(self) -> str:
        policies = {
            c.combo_key: self.get_exit_policy(c.symbol, c.combo_id).to_dict()
            for c in sorted(self.spec().combos, key=lambda x: x.combo_key)
        }
        return canonical_json_sha256({"strategy_id": self.strategy_id, "version": self.version, "exit_policies": policies})

    def get_spec_hash(self) -> str:
        return compute_spec_hash(
            self.get_signal_spec_hash(),
            self.get_exit_spec_hash(),
            self.strategy_id,
            self.version,
        )

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: Collection[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        sym_list = list(symbols) if symbols is not None else list(data.keys())
        base_sigs = super().generate_signals(
            data=data,
            symbols=sym_list,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        kept_non_eth_c1 = [
            s for s in base_sigs if not (s.symbol == "ETH-USDT" and int(s.combo_id) == 1)
        ]

        eth_c1_sigs: list[Signal] = []
        if "ETH-USDT" in sym_list and "ETH-USDT" in data:
            h1_sigs = self._eth_c1_h1.generate_signals(
                data=data,
                symbols=["ETH-USDT"],
                only_approved=only_approved,
                only_warmup_ready=only_warmup_ready,
            )
            eth_c1_sigs = [s for s in h1_sigs if s.symbol == "ETH-USDT" and int(s.combo_id) == 1]

        combined = kept_non_eth_c1 + eth_c1_sigs
        combined.sort(key=lambda s: (int(s.signal_close_ms), s.symbol, int(s.combo_id), s.side))
        return combined


def build_30_combo_catalog(strat: CrossSymbol30FixedStrategy | None = None) -> list[ComboCatalogRow]:
    """Liệt kê chính xác đủ 30 mã kèm nguồn gốc, tham số và đánh dấu 23 mã đang live (Lab 11)."""
    st = strat or CrossSymbol30FixedStrategy()
    base_20_keys = {c.combo_key for c in StrategyCoreRegistry.get("20FINAL", "1").spec().combos}

    rows: list[ComboCatalogRow] = []
    for c in sorted(st.spec().combos, key=lambda x: (x.symbol, int(x.combo_id))):
        ep = st.get_exit_policy(c.symbol, int(c.combo_id))
        is_orig = c.combo_key in base_20_keys
        other_sym = "ETH-USDT" if c.symbol == "BTC-USDT" else "BTC-USDT"
        is_live = (c.symbol, int(c.combo_id)) not in PRUNE23_EXCLUDED_COMBOS

        entry_dict = dict(c.entry)
        entry_variant = c.entry_variant
        if c.symbol == "ETH-USDT" and int(c.combo_id) == 1:
            entry_variant = f"{c.entry_variant} + H1_BOTH_1(slope4h<={H1_BUY_MAX_SLOPE_4H_PCT}, dist4h>={H1_SELL_MIN_DIST_EMA150_4H_PCT}, CCI<-80)"

        is_pos_20 = (
            c.combo_key in AUDIT_20_POSITIVE_KEYS
            or f"{c.symbol}/C{c.combo_id}" in AUDIT_20_POSITIVE_KEYS
        )
        short_sym = c.symbol.split("-")[0]
        rows.append(
            ComboCatalogRow(
                combo_key=c.combo_key,
                combo_symbol=f"C{int(c.combo_id)}-{short_sym}",
                symbol=c.symbol,
                combo_id=int(c.combo_id),
                combo_name=c.combo_name,
                timeframe=c.native_timeframe,
                directions_cross30="MUA+BÁN (BUY+SELL)",
                entry_variant=entry_variant,
                entry_params_json=json.dumps(entry_dict, sort_keys=True, ensure_ascii=False),
                layer2=c.layer2,
                tp_pct=float(ep.tp_pct),
                sl_pct=float(ep.sl_pct),
                origin_type="20FINAL_GỐC" if is_orig else "NHÂN_BẢN_CHÉO_30",
                cloned_from_symbol=None if is_orig else other_sym,
                source_commit_cross30=CROSS30_SOURCE_COMMIT[:7],
                is_live_23=is_live,
                live_status_vi="ĐANG_LIVE_23" if is_live else "NGOÀI_LIVE_7",
                live_source_commit=LIVE23_SOURCE_COMMIT,
                audit_20_status="DƯƠNG_20" if is_pos_20 else "ÂM_10",
            )
        )

    if len(rows) != 30:
        raise RuntimeError(f"Bảng danh mục phải có đủ 30 mã, thực tế: {len(rows)}")
    live_cnt = sum(1 for r in rows if r.is_live_23)
    if live_cnt != 23:
        raise RuntimeError(f"Kỳ vọng 23 mã đang live, thực tế: {live_cnt}")
    return rows


def get_live_23_pairs() -> set[tuple[str, int]]:
    """Trả về tập 23 cặp (symbol, combo_id) đang chạy live trong Lab 11 PRUNE23."""
    st = CrossSymbol30FixedStrategy()
    return {
        (c.symbol, int(c.combo_id))
        for c in st.spec().combos
        if (c.symbol, int(c.combo_id)) not in PRUNE23_EXCLUDED_COMBOS
    }


def get_non_live_7_pairs() -> list[tuple[str, int, str]]:
    """Trả về 7 mã ngoài live được sắp xếp cố định theo (symbol, combo_id)."""
    st = CrossSymbol30FixedStrategy()
    out = [
        (c.symbol, int(c.combo_id), c.combo_key)
        for c in st.spec().combos
        if (c.symbol, int(c.combo_id)) in PRUNE23_EXCLUDED_COMBOS
    ]
    out.sort(key=lambda x: (x[0], x[1]))
    if len(out) != 7:
        raise RuntimeError(f"Kỳ vọng 7 mã ngoài live, thực tế: {len(out)}")
    return out
