"""Phân tích hiệu suất từng mã trong đủ 30 mã theo chế độ thị trường và đặc trưng SMC (chỉ offline)."""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.research_v3_18.combos_30 import (
    ComboCatalogRow,
    CrossSymbol30FixedStrategy,
    build_30_combo_catalog,
)
from autotrade_v3.research_v3_18.regime_features import (
    ALL_REGIME_DIMENSIONS,
    MARKET_REGIME_ORDER,
    attach_regimes_to_trades_df,
    compute_causal_hourly_regimes,
    datetime_index_to_ms,
    load_research_config,
)
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.sim.engine import RiskConfig, SimulationResult, TradeRecord, UnifiedSimulator
from autotrade_v3.strategy.base import Signal

DATASET_START_DATE = "2023-11-01"
DATASET_END_DATE = "2026-11-01"
SPLIT_ORDER: tuple[str, ...] = ("DEVELOPMENT", "VALIDATION", "HOLDOUT")


def _stable_cell_seed(base_seed: int, *parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    offset = int.from_bytes(digest[:4], "little")
    return (int(base_seed) + offset) % (2**31 - 1)


def classify_split(timestamp_ms: int, splits_cfg: dict[str, Any]) -> str:
    """Phân loại mốc thời gian vào 1 trong 3 giai đoạn tĩnh khóa sẵn."""
    ts = int(timestamp_ms)
    for split_name in SPLIT_ORDER:
        bound = splits_cfg[split_name]
        if int(bound["start_ms"]) <= ts <= int(bound["end_ms"]):
            return split_name
    raise ValueError(f"Mốc thời gian {ts} nằm ngoài 3 giai đoạn tĩnh đã khóa")


def compute_closed_drawdown_usd(pnls: np.ndarray) -> float:
    """Tính sụt giảm tối đa trên đường vốn tích lũy theo lệnh đóng (USD)."""
    if pnls.size == 0:
        return 0.0
    cum = np.concatenate(([0.0], np.cumsum(pnls, dtype=float)))
    peaks = np.maximum.accumulate(cum)
    drawdowns = peaks[1:] - cum[1:]
    return float(np.max(drawdowns)) if drawdowns.size > 0 else 0.0


def bootstrap_trade_metrics(
    pnls: np.ndarray,
    *,
    n_boot: int,
    ci_level: float,
    seed: int,
) -> dict[str, list[float] | None]:
    """Tính khoảng tin cậy bootstrap (percentile) cho kỳ vọng lệnh, lãi ròng và tỷ lệ thắng."""
    n = int(pnls.size)
    if n == 0:
        return {
            "ky_vong_lenh_usd_ci95": None,
            "net_pnl_usd_ci95": None,
            "ty_le_thang_pct_ci95": None,
        }
    if n == 1:
        val = float(pnls[0])
        wr = 100.0 if val > 0.0 else 0.0
        return {
            "ky_vong_lenh_usd_ci95": [round(val, 4), round(val, 4)],
            "net_pnl_usd_ci95": [round(val, 4), round(val, 4)],
            "ty_le_thang_pct_ci95": [round(wr, 2), round(wr, 2)],
        }

    rng = np.random.default_rng(int(seed))
    alpha_half = (1.0 - float(ci_level)) / 2.0
    q_low = alpha_half * 100.0
    q_high = (1.0 - alpha_half) * 100.0

    wins = (pnls > 0.0).astype(float)
    boot_means = np.empty(int(n_boot), dtype=float)
    boot_wrs = np.empty(int(n_boot), dtype=float)

    batch_size = max(1, min(int(n_boot), max(1, 200_000 // n)))
    pos = 0
    while pos < int(n_boot):
        cur = min(batch_size, int(n_boot) - pos)
        idx = rng.integers(0, n, size=(cur, n))
        boot_means[pos : pos + cur] = pnls[idx].mean(axis=1)
        boot_wrs[pos : pos + cur] = wins[idx].mean(axis=1) * 100.0
        pos += cur

    exp_lo, exp_hi = np.percentile(boot_means, [q_low, q_high])
    net_lo, net_hi = float(exp_lo) * n, float(exp_hi) * n
    wr_lo, wr_hi = np.percentile(boot_wrs, [q_low, q_high])
    return {
        "ky_vong_lenh_usd_ci95": [round(float(exp_lo), 4), round(float(exp_hi), 4)],
        "net_pnl_usd_ci95": [round(float(net_lo), 4), round(float(net_hi), 4)],
        "ty_le_thang_pct_ci95": [round(float(wr_lo), 2), round(float(wr_hi), 2)],
    }


def summarize_trade_slice(
    df_slice: pd.DataFrame,
    *,
    min_sample_size: int,
    insufficient_label: str,
    n_boot: int,
    ci_level: float,
    seed: int,
) -> dict[str, Any]:
    """Tổng hợp số liệu 1 lát cắt lệnh (mã / chế độ / giai đoạn)."""
    if df_slice.empty:
        return {
            "so_lenh": 0,
            "so_lenh_thang": 0,
            "so_lenh_thua": 0,
            "net_pnl_usd": 0.0,
            "gross_pnl_usd": 0.0,
            "fee_total_usd": 0.0,
            "ty_le_thang_pct": None,
            "pf": None,
            "max_dd_usd": 0.0,
            "ky_vong_lenh_usd": None,
            "ky_vong_lenh_usd_ci95": None,
            "net_pnl_usd_ci95": None,
            "ty_le_thang_pct_ci95": None,
            "status": insufficient_label,
            "conclusive": False,
        }

    ordered = df_slice.sort_values(["exit_time_ms", "entry_time_ms", "trade_id"])
    pnls = ordered["net_pnl"].to_numpy(dtype=float)
    gross_total = float(ordered["gross_pnl"].astype(float).sum())
    fee_total = float(ordered["fee_total"].astype(float).sum())
    net_total = float(np.sum(pnls))
    n = int(pnls.size)
    wins = int(np.sum(pnls > 0.0))
    losses = int(np.sum(pnls < 0.0))
    win_rate = float(wins / n * 100.0)
    gross_profit = float(np.sum(pnls[pnls > 0.0]))
    gross_loss = float(abs(np.sum(pnls[pnls < 0.0])))
    pf: float | None
    if gross_loss > 1e-12:
        pf = round(gross_profit / gross_loss, 4)
    elif gross_profit > 0.0:
        pf = None
    else:
        pf = 0.0

    max_dd = compute_closed_drawdown_usd(pnls)
    expectancy = net_total / n
    ci = bootstrap_trade_metrics(pnls, n_boot=n_boot, ci_level=ci_level, seed=seed)
    conclusive = n >= int(min_sample_size)

    return {
        "so_lenh": n,
        "so_lenh_thang": wins,
        "so_lenh_thua": losses,
        "net_pnl_usd": round(net_total, 4),
        "gross_pnl_usd": round(gross_total, 4),
        "fee_total_usd": round(fee_total, 4),
        "ty_le_thang_pct": round(win_rate, 2),
        "pf": pf,
        "max_dd_usd": round(max_dd, 4),
        "ky_vong_lenh_usd": round(expectancy, 4),
        "ky_vong_lenh_usd_ci95": ci["ky_vong_lenh_usd_ci95"],
        "net_pnl_usd_ci95": ci["net_pnl_usd_ci95"],
        "ty_le_thang_pct_ci95": ci["ty_le_thang_pct_ci95"],
        "status": "ĐỦ_MẪU" if conclusive else insufficient_label,
        "conclusive": conclusive,
    }


def trades_to_dataframe(
    trades: list[TradeRecord],
    signal_lookup: dict[str, Signal] | None = None,
) -> pd.DataFrame:
    """Chuyển danh sách `TradeRecord` sang DataFrame kèm `combo_key` và `signal_close_ms`."""
    if not trades:
        return pd.DataFrame(
            columns=[
                "trade_id",
                "leg_id",
                "symbol",
                "combo_id",
                "combo_key",
                "combo_name",
                "timeframe",
                "side",
                "entry_time_ms",
                "exit_time_ms",
                "notional",
                "gross_pnl",
                "fee_total",
                "net_pnl",
                "pnl_own_entry",
                "signal_id",
                "signal_close_ms",
            ]
        )
    rows: list[dict[str, Any]] = []
    for t in trades:
        row = asdict(t)
        sig = signal_lookup.get(str(t.signal_id)) if signal_lookup is not None else None
        row["combo_key"] = f"{t.symbol}_C{int(t.combo_id)}"
        row["signal_close_ms"] = int(sig.signal_close_ms) if sig is not None else (int(t.entry_time_ms) - 1)
        rows.append(row)
    return pd.DataFrame(rows)


def load_offline_market_context(
    *,
    config: dict[str, Any] | None = None,
    data_layer: DataLayer | None = None,
) -> dict[str, Any]:
    """Tải dữ liệu M1/M15 ngoại tuyến và dựng đặc trưng chế độ thị trường/SMC nhân quả."""
    cfg = config or load_research_config()
    dl = data_layer or DataLayer()
    symbols = list(cfg["dataset"]["symbols"])
    start_date = str(cfg["dataset"].get("start_date", DATASET_START_DATE))
    end_date = str(cfg["dataset"].get("end_date", DATASET_END_DATE))

    start_ms = int(pd.Timestamp(f"{start_date}T00:00:00Z").timestamp() * 1000)
    end_ms_excl = int(pd.Timestamp(f"{end_date}T00:00:00Z").timestamp() * 1000)

    m15_warmup: dict[str, pd.DataFrame] = {}
    m1_eval: dict[str, pd.DataFrame] = {}
    hourly_regimes: dict[str, pd.DataFrame] = {}

    for sym in symbols:
        df15 = dl.query_timeframe(sym, "15m", start_date, end_date, include_warmup=True)
        df1_raw = dl.query_m1(sym, start_date, end_date, include_warmup=False)
        t_ms = datetime_index_to_ms(df1_raw.index)
        df1 = df1_raw.loc[(t_ms >= start_ms) & (t_ms < end_ms_excl)].copy()
        m15_warmup[sym] = df15
        m1_eval[sym] = df1
        hourly_regimes[sym] = compute_causal_hourly_regimes(df15, config=cfg, enforce_continuity=True)

    strat = CrossSymbol30FixedStrategy()
    catalog = build_30_combo_catalog(strat)
    raw_signals = strat.generate_signals(m15_warmup, symbols=symbols, only_approved=True, only_warmup_ready=True)
    eval_signals = [s for s in raw_signals if start_ms <= int(s.signal_close_ms) < end_ms_excl]
    eval_signals.sort(key=lambda s: (int(s.signal_close_ms), s.symbol, int(s.combo_id), s.side))

    return {
        "config": cfg,
        "symbols": symbols,
        "start_ms": start_ms,
        "end_ms_excl": end_ms_excl,
        "m15_warmup": m15_warmup,
        "m1_eval": m1_eval,
        "hourly_regimes": hourly_regimes,
        "strategy": strat,
        "catalog": catalog,
        "eval_signals": eval_signals,
    }


def make_base_simulator(config: dict[str, Any]) -> UnifiedSimulator:
    """Khởi tạo UnifiedSimulator cấu hình BASE thống nhất từ config.json."""
    sim_cfg = config["simulation"]
    cost_profile = str(sim_cfg["cost_profile"])
    cm = CostModel.from_profile_name(cost_profile, is_real_data=True)
    risk = RiskConfig(
        initial_equity=float(sim_cfg["initial_equity"]),
        notional_per_trade=float(sim_cfg["notional_per_trade"]),
        slippage_rate=0.0,
        taker_fee_rate=float(sim_cfg["taker_fee_rate"]),
        cost_profile=cost_profile,
        cost_model=cm,
    )
    return UnifiedSimulator(
        risk_config=risk,
        final14_exit_mode="50TP_50TRAIL",
        cost_model=cm,
        ambiguity_policy=str(sim_cfg["ambiguity_policy"]),
    )


def run_combo_regime_analysis(
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """Chạy ALL_SIGNALS (BASE) cho đủ 30 mã và tổng hợp bảng mã × chế độ."""
    cfg = ctx["config"]
    sim = make_base_simulator(cfg)
    eval_signals: list[Signal] = ctx["eval_signals"]
    m1_eval: dict[str, pd.DataFrame] = ctx["m1_eval"]
    hourly_regimes: dict[str, pd.DataFrame] = ctx["hourly_regimes"]
    catalog: list[ComboCatalogRow] = ctx["catalog"]

    sim_res: SimulationResult = sim.run("ALL_SIGNALS", eval_signals, m1_eval)
    signal_lookup = {str(s.signal_id): s for s in eval_signals if s.signal_id}
    trades_raw = trades_to_dataframe(sim_res.trades, signal_lookup=signal_lookup)
    trades_annotated = attach_regimes_to_trades_df(trades_raw, hourly_regimes, config=cfg)

    splits_cfg = cfg["splits"]
    trades_annotated["split"] = [
        classify_split(int(ts), splits_cfg)
        for ts in trades_annotated["signal_close_ms"].tolist()
    ]

    stats_cfg = cfg["statistics"]
    min_n = int(stats_cfg["min_sample_size"])
    insuff = str(stats_cfg["insufficient_sample_label"])
    n_boot = int(stats_cfg["bootstrap_iterations"])
    ci_level = float(stats_cfg["bootstrap_ci_level"])
    base_seed = int(stats_cfg["bootstrap_seed"])

    regime_keys: list[str] = list(MARKET_REGIME_ORDER)
    extra_regimes = sorted(
        set(trades_annotated["che_do_thi_truong"].astype(str).unique()).difference(regime_keys)
    )
    regime_keys.extend(extra_regimes)

    per_combo_rows: list[dict[str, Any]] = []
    dev_positive_regimes_by_combo: dict[str, list[str]] = {}
    dev_bad_regimes_by_combo: dict[str, list[str]] = {}
    min_dev_pnl = float(cfg["portfolio_selection_rules"]["dev_filter_min_net_pnl"])
    bad_dev_pnl = float(cfg["portfolio_selection_rules"]["dev_bad_regime_max_net_pnl"])

    for item in catalog:
        c_df = trades_annotated.loc[trades_annotated["combo_key"] == item.combo_key]
        overall_by_split: dict[str, Any] = {}
        for sp_name in ("ALL",) + SPLIT_ORDER:
            sp_df = c_df if sp_name == "ALL" else c_df.loc[c_df["split"] == sp_name]
            seed = _stable_cell_seed(base_seed, item.combo_key, "OVERALL", sp_name)
            overall_by_split[sp_name] = summarize_trade_slice(
                sp_df,
                min_sample_size=min_n,
                insufficient_label=insuff,
                n_boot=n_boot,
                ci_level=ci_level,
                seed=seed,
            )

        by_regime_all: dict[str, Any] = {}
        by_regime_splits: dict[str, dict[str, Any]] = {sp: {} for sp in SPLIT_ORDER}
        pos_dev: list[str] = []
        bad_dev: list[str] = []

        for reg_name in regime_keys:
            r_df = c_df.loc[c_df["che_do_thi_truong"] == reg_name]
            if r_df.empty and reg_name not in MARKET_REGIME_ORDER:
                continue
            all_cell = summarize_trade_slice(
                r_df,
                min_sample_size=min_n,
                insufficient_label=insuff,
                n_boot=n_boot,
                ci_level=ci_level,
                seed=_stable_cell_seed(base_seed, item.combo_key, reg_name, "ALL"),
            )
            by_regime_all[reg_name] = all_cell

            for sp_name in SPLIT_ORDER:
                rsp_df = r_df.loc[r_df["split"] == sp_name]
                sp_cell = summarize_trade_slice(
                    rsp_df,
                    min_sample_size=min_n,
                    insufficient_label=insuff,
                    n_boot=n_boot,
                    ci_level=ci_level,
                    seed=_stable_cell_seed(base_seed, item.combo_key, reg_name, sp_name),
                )
                by_regime_splits[sp_name][reg_name] = sp_cell
                if sp_name == "DEVELOPMENT" and sp_cell["conclusive"]:
                    if float(sp_cell["net_pnl_usd"]) > min_dev_pnl:
                        pos_dev.append(reg_name)
                    if float(sp_cell["net_pnl_usd"]) <= bad_dev_pnl:
                        bad_dev.append(reg_name)

        dev_positive_regimes_by_combo[item.combo_key] = pos_dev
        dev_bad_regimes_by_combo[item.combo_key] = bad_dev

        per_combo_rows.append(
            {
                "catalog": asdict(item),
                "overall_all": overall_by_split["ALL"],
                "overall_by_split": {sp: overall_by_split[sp] for sp in SPLIT_ORDER},
                "by_regime": by_regime_all,
                "by_regime_splits": by_regime_splits,
                "dev_positive_regimes": pos_dev,
                "dev_bad_regimes": bad_dev,
            }
        )

    dimension_summaries: dict[str, list[dict[str, Any]]] = {}
    for dim in ALL_REGIME_DIMENSIONS:
        dim_rows: list[dict[str, Any]] = []
        values = list(MARKET_REGIME_ORDER) if dim == "che_do_thi_truong" else sorted(
            trades_annotated[dim].astype(str).unique()
        )
        for v in sorted(set(values).union(set(trades_annotated[dim].astype(str).unique())), key=lambda x: (values.index(x) if x in values else 999, x)):
            v_df = trades_annotated.loc[trades_annotated[dim] == v]
            row_entry: dict[str, Any] = {
                "dimension": dim,
                "label": v,
                "all": summarize_trade_slice(
                    v_df,
                    min_sample_size=min_n,
                    insufficient_label=insuff,
                    n_boot=n_boot,
                    ci_level=ci_level,
                    seed=_stable_cell_seed(base_seed, "DIM", dim, v, "ALL"),
                ),
            }
            for sp_name in SPLIT_ORDER:
                vsp_df = v_df.loc[v_df["split"] == sp_name]
                row_entry[sp_name] = summarize_trade_slice(
                    vsp_df,
                    min_sample_size=min_n,
                    insufficient_label=insuff,
                    n_boot=n_boot,
                    ci_level=ci_level,
                    seed=_stable_cell_seed(base_seed, "DIM", dim, v, sp_name),
                )
            dim_rows.append(row_entry)
        dimension_summaries[dim] = dim_rows

    overall_portfolio_base = {
        sp_name: summarize_trade_slice(
            trades_annotated if sp_name == "ALL" else trades_annotated.loc[trades_annotated["split"] == sp_name],
            min_sample_size=min_n,
            insufficient_label=insuff,
            n_boot=n_boot,
            ci_level=ci_level,
            seed=_stable_cell_seed(base_seed, "ALL_COMBOS_ALL_SIGNALS", sp_name),
        )
        for sp_name in ("ALL",) + SPLIT_ORDER
    }

    return {
        "regime_order": list(MARKET_REGIME_ORDER) + [r for r in extra_regimes if not trades_annotated.loc[trades_annotated["che_do_thi_truong"] == r].empty],
        "overall_all_signals": overall_portfolio_base,
        "total_eval_signals": len(eval_signals),
        "total_trades_all_signals": int(len(trades_annotated)),
        "per_combo": per_combo_rows,
        "dimension_summaries": dimension_summaries,
        "dev_positive_regimes_by_combo": dev_positive_regimes_by_combo,
        "dev_bad_regimes_by_combo": dev_bad_regimes_by_combo,
        "trades_annotated": trades_annotated,
    }
