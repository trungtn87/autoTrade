"""Thí nghiệm ghép danh mục song song (`ALL_SIGNALS_PORTFOLIO`) và kiểm định nhiều phép thử."""
from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.analysis.metrics import compute_concentration_metrics
from autotrade_v3.reconcile_v3.era_engines import ensure_fixed_v1_registered
from autotrade_v3.research_v3_18.combo_regime_analyzer import (
    SPLIT_ORDER,
    classify_split,
    make_base_simulator,
    trades_to_dataframe,
)
from autotrade_v3.research_v3_18.combos_30 import (
    AUDIT_20_POSITIVE_KEYS,
    ComboCatalogRow,
    get_live_23_pairs,
    get_non_live_7_pairs,
)
from autotrade_v3.research_v3_18.regime_features import map_timestamp_to_regime
from autotrade_v3.sim.engine import SimulationResult
from autotrade_v3.strategy.base import Signal
from autotrade_v3.strategy.v3_lab_11_prune23 import V3Lab11Prune23Strategy


def _pair_seed(base_seed: int, trial_id: str, split_name: str) -> int:
    digest = hashlib.sha256(f"{trial_id}|{split_name}".encode("utf-8")).digest()
    return (int(base_seed) + int.from_bytes(digest[:4], "little")) % (2**31 - 1)


def _short_combo_label(symbol: str, combo_id: int) -> str:
    prefix = symbol.split("-")[0]
    if combo_id >= 100:
        return f"{prefix}_C{combo_id - 90}"
    return f"{prefix}_C{combo_id}"


def _combo_key(symbol: str, combo_id: int) -> str:
    return f"{symbol}_C{int(combo_id)}"


def _compute_max_concurrent_positions(sub_trades: pd.DataFrame) -> int:
    if sub_trades.empty:
        return 0
    entries = (
        sub_trades.groupby("trade_id", as_index=False)
        .agg(entry_time_ms=("entry_time_ms", "min"), exit_time_ms=("exit_time_ms", "max"))
    )
    events: list[tuple[int, int]] = []
    for _, row in entries.iterrows():
        events.append((int(row["entry_time_ms"]), 1))
        events.append((int(row["exit_time_ms"]), -1))
    events.sort(key=lambda x: (x[0], x[1]))
    cur = 0
    max_pos = 0
    for _, delta in events:
        cur += delta
        if cur > max_pos:
            max_pos = cur
    return int(max_pos)


def _compute_split_m2m_drawdown(
    snapshots: pd.DataFrame,
    *,
    initial_equity: float,
    start_ms: int | None,
    end_ms: int | None,
) -> tuple[float, float]:
    if snapshots.empty:
        return 0.0, 0.0
    if start_ms is None or end_ms is None:
        dd_usd = float(snapshots["drawdown_dollars"].max())
        dd_pct = float(snapshots["drawdown_pct"].max())
        return round(dd_usd, 4), round(dd_pct, 2)

    ts = snapshots["timestamp_ms"].to_numpy(dtype=np.int64)
    in_win = (ts >= int(start_ms)) & (ts <= int(end_ms))
    sp_sn = snapshots.loc[in_win]
    if sp_sn.empty:
        return 0.0, 0.0
    prior_sn = snapshots.loc[ts < int(start_ms)]
    eq_before = float(prior_sn["equity"].iloc[-1]) if not prior_sn.empty else float(initial_equity)

    eq_vals = sp_sn["equity"].to_numpy(dtype=float)
    eq_norm = float(initial_equity) + (eq_vals - eq_before)
    series = np.concatenate(([float(initial_equity)], eq_norm))
    peaks = np.maximum.accumulate(series)
    dd_arr = peaks[1:] - series[1:]
    dd_pct_arr = np.where(peaks[1:] > 1e-9, dd_arr / peaks[1:] * 100.0, 0.0)
    return round(float(np.max(dd_arr)), 4), round(float(np.max(dd_pct_arr)), 2)


def _summarize_portfolio_slice(
    sub_trades: pd.DataFrame,
    snapshots: pd.DataFrame,
    *,
    initial_equity: float,
    start_ms: int | None,
    end_ms: int | None,
) -> dict[str, Any]:
    m2m_dd_usd, m2m_dd_pct = _compute_split_m2m_drawdown(
        snapshots,
        initial_equity=initial_equity,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    if sub_trades.empty:
        return {
            "so_lenh": 0,
            "net_pnl_usd": 0.0,
            "roi_pct": 0.0,
            "ty_le_thang_pct": None,
            "pf": None,
            "m2m_max_dd_usd": m2m_dd_usd,
            "m2m_max_dd_pct": m2m_dd_pct,
            "max_concurrent_positions": 0,
            "max_open_notional_total": 0.0,
            "top_combo_key": None,
            "top_combo_net_pnl_usd": 0.0,
            "max_single_combo_share_pct": None,
            "btc_c11_share_pct": None,
        }

    net_vals = sub_trades["net_pnl"].to_numpy(dtype=float)
    net_total = float(np.sum(net_vals))
    n = int(net_vals.size)
    wins = int(np.sum(net_vals > 0.0))
    win_rate = round(wins / n * 100.0, 2)
    gross_profit = float(np.sum(net_vals[net_vals > 0.0]))
    gross_loss = float(abs(np.sum(net_vals[net_vals < 0.0])))
    pf: float | None = round(gross_profit / gross_loss, 4) if gross_loss > 1e-12 else (None if gross_profit > 0 else 0.0)

    conc = compute_concentration_metrics(sub_trades, execution_mode="ALL_SIGNALS_PORTFOLIO")
    max_concurrent = _compute_max_concurrent_positions(sub_trades)

    basis_col = "pnl_own_entry" if "pnl_own_entry" in sub_trades.columns else "net_pnl"
    by_combo = sub_trades.groupby("combo_key")[basis_col].sum().sort_values(ascending=False)
    top_combo_key = str(by_combo.index[0]) if not by_combo.empty else None
    top_combo_pnl = float(by_combo.iloc[0]) if not by_combo.empty else 0.0
    if net_total > 1e-9 and top_combo_key is not None:
        max_share_pct: float | None = round(top_combo_pnl / net_total * 100.0, 2)
    else:
        max_share_pct = None

    btc_c11_share = conc.get("btc_c11_share_of_total_net_pct")
    return {
        "so_lenh": n,
        "net_pnl_usd": round(net_total, 4),
        "roi_pct": round(net_total / float(initial_equity) * 100.0, 2),
        "ty_le_thang_pct": win_rate,
        "pf": pf,
        "m2m_max_dd_usd": m2m_dd_usd,
        "m2m_max_dd_pct": m2m_dd_pct,
        "max_concurrent_positions": max_concurrent,
        "max_open_notional_total": round(float(conc.get("max_open_notional_total", 0.0)), 2),
        "top_combo_key": top_combo_key,
        "top_combo_net_pnl_usd": round(top_combo_pnl, 4),
        "max_single_combo_share_pct": max_share_pct,
        "btc_c11_share_pct": round(float(btc_c11_share), 2) if btc_c11_share is not None else None,
    }


def _daily_pnl_series(
    trades_df: pd.DataFrame,
    split_name: str,
    splits_cfg: dict[str, Any],
) -> np.ndarray:
    bound = splits_cfg[split_name]
    start_dt = pd.to_datetime(int(bound["start_ms"]), unit="ms", utc=True).normalize()
    end_dt = pd.to_datetime(int(bound["end_ms"]), unit="ms", utc=True).normalize()
    dt_col = pd.date_range(start_dt, end_dt, freq="D", tz="UTC")
    day_strs = dt_col.strftime("%Y-%m-%d")

    if trades_df.empty or "split" not in trades_df.columns:
        return np.zeros(len(day_strs), dtype=float)

    sub = trades_df.loc[trades_df["split"] == split_name]
    if sub.empty:
        return np.zeros(len(day_strs), dtype=float)

    dt_series = pd.to_datetime(sub["signal_close_ms"].to_numpy(dtype=np.int64), unit="ms", utc=True).strftime("%Y-%m-%d")
    grouped = (
        pd.DataFrame({"day": dt_series, "net_pnl": sub["net_pnl"].to_numpy(dtype=float)})
        .groupby("day")["net_pnl"]
        .sum()
    )
    aligned = grouped.reindex(day_strs, fill_value=0.0)
    return aligned.to_numpy(dtype=float)


def _paired_daily_bootstrap_pvalue(
    trial_daily: np.ndarray,
    base_daily: np.ndarray,
    *,
    n_boot: int,
    seed: int,
) -> float:
    diff = trial_daily - base_daily
    if np.allclose(diff, 0.0):
        return 1.0
    n = int(diff.size)
    rng = np.random.default_rng(int(seed))
    idx = rng.integers(0, n, size=(int(n_boot), n))
    boot_sums = diff[idx].sum(axis=1)
    p_val = (float(np.sum(boot_sums <= 0.0)) + 1.0) / (float(n_boot) + 1.0)
    return round(p_val, 6)


def _apply_holm_bonferroni(p_values: dict[str, float]) -> dict[str, float]:
    if not p_values:
        return {}
    ordered = sorted(p_values.items(), key=lambda kv: kv[1])
    m = len(ordered)
    adjusted: dict[str, float] = {}
    running_max = 0.0
    for rank, (k, p_raw) in enumerate(ordered):
        mult = m - rank
        adj = min(1.0, max(running_max, mult * float(p_raw)))
        running_max = adj
        adjusted[k] = round(adj, 6)
    return adjusted


def _downsample_equity_curve(snapshots: pd.DataFrame) -> list[dict[str, Any]]:
    if snapshots.empty:
        return []
    ts_utc = pd.to_datetime(snapshots["timestamp_ms"].to_numpy(dtype=np.int64), unit="ms", utc=True)
    snap = pd.DataFrame(
        {
            "date": ts_utc.strftime("%Y-%m-%d"),
            "timestamp_ms": snapshots["timestamp_ms"].to_numpy(dtype=np.int64),
            "equity": snapshots["equity"].to_numpy(dtype=float),
        }
    )
    daily_last = snap.groupby("date", as_index=False).last()
    if len(daily_last) > 180:
        step = max(1, len(daily_last) // 180)
        sampled = daily_last.iloc[::step].copy()
        if sampled.iloc[-1]["timestamp_ms"] != daily_last.iloc[-1]["timestamp_ms"]:
            sampled = pd.concat([sampled, daily_last.iloc[[-1]]], ignore_index=True)
        daily_last = sampled
    return [
        {
            "date": str(r["date"]),
            "timestamp_ms": int(r["timestamp_ms"]),
            "equity": round(float(r["equity"]), 2),
        }
        for _, r in daily_last.iterrows()
    ]


def evaluate_portfolio_candidate_flag(
    *,
    val_delta: float,
    hold_delta: float,
    val_dd: float,
    hold_dd: float,
    base_val_dd: float,
    base_hold_dd: float,
    dd_multiplier: float,
) -> bool:
    """Quy tắc ứng viên ghép danh mục: bắt buộc cải thiện trên CẢ KIỂM_ĐỊNH và KIỂM_CHỨNG_CUỐI."""
    return (
        float(val_delta) > 0.0
        and float(hold_delta) > 0.0
        and float(val_dd) <= float(base_val_dd) * float(dd_multiplier)
        and float(hold_dd) <= float(base_hold_dd) * float(dd_multiplier)
    )


def run_portfolio_experiments(
    ctx: dict[str, Any],
    combo_analysis: dict[str, Any],
) -> dict[str, Any]:
    """Chạy các phép thử ghép mã song song trên `ALL_SIGNALS_PORTFOLIO` và kiểm định nhiều phép thử."""
    cfg = ctx["config"]
    sim_cfg = cfg["simulation"]
    splits_cfg = cfg["splits"]
    stats_cfg = cfg["statistics"]
    rules_cfg = cfg["portfolio_selection_rules"]

    initial_equity = float(sim_cfg["initial_equity"])
    n_boot = int(stats_cfg["bootstrap_iterations"])
    base_seed = int(stats_cfg["bootstrap_seed"])
    alpha = float(stats_cfg["family_wise_alpha"])
    dd_mult = float(rules_cfg["max_dd_multiplier_vs_baseline"])

    eval_signals: list[Signal] = ctx["eval_signals"]
    m1_eval: dict[str, pd.DataFrame] = ctx["m1_eval"]
    m15_warmup: dict[str, pd.DataFrame] = ctx["m15_warmup"]
    hourly_regimes: dict[str, pd.DataFrame] = ctx["hourly_regimes"]
    catalog: list[ComboCatalogRow] = ctx["catalog"]
    symbols: list[str] = ctx["symbols"]
    start_ms = int(ctx["start_ms"])
    end_ms_excl = int(ctx["end_ms_excl"])

    sessions_cfg = dict(cfg["regime_params"]["sessions_utc"])
    signal_regime_map: dict[str, str] = {}
    for sig in eval_signals:
        reg_info = map_timestamp_to_regime(
            hourly_regimes[sig.symbol],
            int(sig.signal_close_ms),
            sessions_cfg=sessions_cfg,
        )
        signal_regime_map[str(sig.signal_id)] = str(reg_info["che_do_thi_truong"])

    live23_pairs = get_live_23_pairs()
    non_live7 = get_non_live_7_pairs()
    dev_pos_map: dict[str, list[str]] = combo_analysis["dev_positive_regimes_by_combo"]
    dev_bad_map: dict[str, list[str]] = combo_analysis["dev_bad_regimes_by_combo"]

    live23_sigs = [s for s in eval_signals if (s.symbol, int(s.combo_id)) in live23_pairs]

    specs: list[dict[str, Any]] = []
    specs.append(
        {
            "trial_id": "DANH_MỤC_23_MÃ_LIVE_CHÉO_30",
            "category_vi": "CƠ_SỞ_LIVE_23",
            "description_vi": "23 mã đang chạy live trích xuất từ tập 30 mã chuẩn Fixed TP/SL",
            "is_baseline": True,
            "target_combo_key": None,
            "signals": live23_sigs,
            "status_note": "ĐỦ_TÍN_HIỆU",
            "qualifying_dev_regimes": None,
        }
    )

    for sym, cid, origin_desc in non_live7:
        ckey = _combo_key(sym, cid)
        short = _short_combo_label(sym, cid)
        added_sigs = [s for s in eval_signals if s.symbol == sym and int(s.combo_id) == int(cid)]
        raw_sigs = sorted(live23_sigs + added_sigs, key=lambda s: (int(s.signal_close_ms), s.symbol, int(s.combo_id), s.side))
        specs.append(
            {
                "trial_id": f"GHÉP_THÔ_{short}",
                "category_vi": "GHÉP_THÔ_1_MÃ_NGOÀI_LIVE",
                "description_vi": f"23 mã live + {sym}/C{cid} (toàn bộ tín hiệu · {origin_desc})",
                "is_baseline": False,
                "target_combo_key": ckey,
                "signals": raw_sigs,
                "status_note": "ĐỦ_TÍN_HIỆU",
                "qualifying_dev_regimes": None,
            }
        )

        pos_regs = list(dev_pos_map.get(ckey, []))
        if pos_regs:
            filt_added = [s for s in added_sigs if signal_regime_map[str(s.signal_id)] in pos_regs]
            filt_sigs = sorted(live23_sigs + filt_added, key=lambda s: (int(s.signal_close_ms), s.symbol, int(s.combo_id), s.side))
            status_note = "LỌC_THEO_DEV_DƯƠNG" if len(filt_added) < len(added_sigs) else "TOÀN_BỘ_CHẾ_ĐỘ_ĐẠT_DEV"
        else:
            filt_sigs = live23_sigs
            status_note = "KHÔNG_CÓ_CHẾ_ĐỘ_ĐẠT_DEV"

        specs.append(
            {
                "trial_id": f"GHÉP_LỌC_DEV_{short}",
                "category_vi": "GHÉP_LỌC_CHẾ_ĐỘ_DEV",
                "description_vi": (
                    f"23 mã live + {sym}/C{cid} chỉ giữ chế độ dương ở PHÁT_TRIỂN: "
                    + (", ".join(pos_regs) if pos_regs else "không có")
                ),
                "is_baseline": False,
                "target_combo_key": ckey,
                "signals": filt_sigs,
                "status_note": status_note,
                "qualifying_dev_regimes": pos_regs,
            }
        )

    audit20_sigs = [s for s in eval_signals if _combo_key(s.symbol, int(s.combo_id)) in AUDIT_20_POSITIVE_KEYS]
    specs.append(
        {
            "trial_id": "DANH_MỤC_30_MÃ_ĐẦY_ĐỦ",
            "category_vi": "NHÓM_DANH_MỤC",
            "description_vi": "Toàn bộ 30 mã đợt chạy chéo 30 chạy song song không lọc chế độ",
            "is_baseline": False,
            "target_combo_key": None,
            "signals": eval_signals,
            "status_note": "ĐỦ_TÍN_HIỆU",
            "qualifying_dev_regimes": None,
        }
    )
    specs.append(
        {
            "trial_id": "DANH_MỤC_20_MÃ_DƯƠNG_AUDIT",
            "category_vi": "NHÓM_DANH_MỤC",
            "description_vi": "Nhóm 20 mã dương (AUDIT_20_POSITIVE_FIXED_TPSL_20261116)",
            "is_baseline": False,
            "target_combo_key": None,
            "signals": audit20_sigs,
            "status_note": "ĐỦ_TÍN_HIỆU",
            "qualifying_dev_regimes": None,
        }
    )

    live23_no_bad_dev = [
        s for s in live23_sigs
        if signal_regime_map[str(s.signal_id)] not in set(dev_bad_map.get(_combo_key(s.symbol, int(s.combo_id)), []))
    ]
    specs.append(
        {
            "trial_id": "DANH_MỤC_23_LIVE_LỌC_BỎ_CHẾ_ĐỘ_XẤU_DEV",
            "category_vi": "NHÓM_LỌC_CHẾ_ĐỘ_DEV",
            "description_vi": "23 mã live loại bỏ các chế độ có >=30 lệnh và lỗ <= -2 USD trên PHÁT_TRIỂN",
            "is_baseline": False,
            "target_combo_key": None,
            "signals": live23_no_bad_dev,
            "status_note": "LỌC_THEO_DEV",
            "qualifying_dev_regimes": None,
        }
    )

    all30_dev_pos = [
        s for s in eval_signals
        if signal_regime_map[str(s.signal_id)] in set(dev_pos_map.get(_combo_key(s.symbol, int(s.combo_id)), []))
    ]
    specs.append(
        {
            "trial_id": "DANH_MỤC_30_MÃ_LỌC_CHẾ_ĐỘ_DƯƠNG_DEV",
            "category_vi": "NHÓM_LỌC_CHẾ_ĐỘ_DEV",
            "description_vi": "30 mã chỉ giữ các chế độ có >=30 lệnh và lãi > 0 USD trên PHÁT_TRIỂN",
            "is_baseline": False,
            "target_combo_key": None,
            "signals": all30_dev_pos,
            "status_note": "LỌC_THEO_DEV",
            "qualifying_dev_regimes": None,
        }
    )

    ensure_fixed_v1_registered()
    prune23_strat = V3Lab11Prune23Strategy()
    prune23_raw = prune23_strat.generate_signals(m15_warmup, symbols=symbols, only_approved=True, only_warmup_ready=True)
    prune23_eval = [s for s in prune23_raw if start_ms <= int(s.signal_close_ms) < end_ms_excl]
    prune23_eval.sort(key=lambda s: (int(s.signal_close_ms), s.symbol, int(s.combo_id), s.side))
    specs.append(
        {
            "trial_id": "DANH_MỤC_23_LAB11_PRUNE23_CHUẨN",
            "category_vi": "THAM_CHIẾU_LIVE_LAB11",
            "description_vi": "Chiến lược live chuẩn V3_LAB_11_PRUNE23@11.0.0 (23 mã, bao gồm cập nhật Lab 5/8/10/11)",
            "is_baseline": False,
            "target_combo_key": None,
            "signals": prune23_eval,
            "status_note": "THAM_CHIẾU_LIVE",
            "qualifying_dev_regimes": None,
        }
    )

    sim = make_base_simulator(cfg)
    sim_cache: dict[tuple[str, ...], tuple[str, SimulationResult, pd.DataFrame]] = {}
    evaluated_runs: list[dict[str, Any]] = []

    for sp in specs:
        sigs: list[Signal] = sp["signals"]
        sig_key = tuple(str(s.signal_id) for s in sigs)
        reused_from: str | None = None
        if sig_key in sim_cache:
            reused_from, sim_res, tdf = sim_cache[sig_key]
        else:
            sim_res = sim.run("ALL_SIGNALS_PORTFOLIO", sigs, m1_eval)
            sig_lookup = {str(s.signal_id): s for s in sigs if s.signal_id}
            tdf = trades_to_dataframe(sim_res.trades, signal_lookup=sig_lookup)
            if not tdf.empty:
                tdf["split"] = [classify_split(int(ts), splits_cfg) for ts in tdf["signal_close_ms"].tolist()]
            else:
                tdf["split"] = pd.Series(dtype=str)
            sim_cache[sig_key] = (str(sp["trial_id"]), sim_res, tdf)

        split_stats: dict[str, Any] = {
            "ALL": _summarize_portfolio_slice(
                tdf,
                sim_res.snapshots,
                initial_equity=initial_equity,
                start_ms=None,
                end_ms=None,
            )
        }
        for sp_name in SPLIT_ORDER:
            b = splits_cfg[sp_name]
            sp_tdf = tdf.loc[tdf["split"] == sp_name] if not tdf.empty else tdf
            split_stats[sp_name] = _summarize_portfolio_slice(
                sp_tdf,
                sim_res.snapshots,
                initial_equity=initial_equity,
                start_ms=int(b["start_ms"]),
                end_ms=int(b["end_ms"]),
            )

        evaluated_runs.append(
            {
                "trial_id": sp["trial_id"],
                "category_vi": sp["category_vi"],
                "description_vi": sp["description_vi"],
                "is_baseline": bool(sp["is_baseline"]),
                "target_combo_key": sp["target_combo_key"],
                "signal_count": len(sigs),
                "accepted_signals": len(sim_res.accepted_signals),
                "skipped_signals": len(sim_res.skipped_signals),
                "rejected_signals": len(sim_res.rejected_signals),
                "status_note": sp["status_note"],
                "qualifying_dev_regimes": sp["qualifying_dev_regimes"],
                "sim_reused_from": reused_from,
                "metrics": split_stats,
                "equity_curve": _downsample_equity_curve(sim_res.snapshots),
                "_trades_df": tdf,
            }
        )

    baseline = next(r for r in evaluated_runs if r["is_baseline"])
    base_tdf: pd.DataFrame = baseline["_trades_df"]
    base_val_daily = _daily_pnl_series(base_tdf, "VALIDATION", splits_cfg)
    base_hold_daily = _daily_pnl_series(base_tdf, "HOLDOUT", splits_cfg)
    base_val_net = float(baseline["metrics"]["VALIDATION"]["net_pnl_usd"])
    base_hold_net = float(baseline["metrics"]["HOLDOUT"]["net_pnl_usd"])
    base_dev_net = float(baseline["metrics"]["DEVELOPMENT"]["net_pnl_usd"])
    base_all_net = float(baseline["metrics"]["ALL"]["net_pnl_usd"])
    base_val_dd = float(baseline["metrics"]["VALIDATION"]["m2m_max_dd_usd"])
    base_hold_dd = float(baseline["metrics"]["HOLDOUT"]["m2m_max_dd_usd"])

    p_val_raw: dict[str, float] = {}
    p_hold_raw: dict[str, float] = {}
    non_baseline = [r for r in evaluated_runs if not r["is_baseline"]]

    for r in non_baseline:
        tid = str(r["trial_id"])
        tdf = r["_trades_df"]
        p_val_raw[tid] = _paired_daily_bootstrap_pvalue(
            _daily_pnl_series(tdf, "VALIDATION", splits_cfg),
            base_val_daily,
            n_boot=n_boot,
            seed=_pair_seed(base_seed, tid, "VALIDATION"),
        )
        p_hold_raw[tid] = _paired_daily_bootstrap_pvalue(
            _daily_pnl_series(tdf, "HOLDOUT", splits_cfg),
            base_hold_daily,
            n_boot=n_boot,
            seed=_pair_seed(base_seed, tid, "HOLDOUT"),
        )

    p_val_holm = _apply_holm_bonferroni(p_val_raw)
    p_hold_holm = _apply_holm_bonferroni(p_hold_raw)
    total_trials_m = len(non_baseline)

    portfolio_rows: list[dict[str, Any]] = []
    candidate_watchlist: list[dict[str, Any]] = []
    catalog_by_key = {row.combo_key: row for row in catalog}

    for r in evaluated_runs:
        tid = str(r["trial_id"])
        m = r["metrics"]
        dev_delta = round(float(m["DEVELOPMENT"]["net_pnl_usd"]) - base_dev_net, 4)
        val_delta = round(float(m["VALIDATION"]["net_pnl_usd"]) - base_val_net, 4)
        hold_delta = round(float(m["HOLDOUT"]["net_pnl_usd"]) - base_hold_net, 4)
        all_delta = round(float(m["ALL"]["net_pnl_usd"]) - base_all_net, 4)
        val_dd = float(m["VALIDATION"]["m2m_max_dd_usd"])
        hold_dd = float(m["HOLDOUT"]["m2m_max_dd_usd"])

        if r["is_baseline"]:
            improved = False
            pv = None
            ph = None
            pv_adj = None
            ph_adj = None
            holm_sig_both = False
        else:
            improved = evaluate_portfolio_candidate_flag(
                val_delta=val_delta,
                hold_delta=hold_delta,
                val_dd=val_dd,
                hold_dd=hold_dd,
                base_val_dd=base_val_dd,
                base_hold_dd=base_hold_dd,
                dd_multiplier=dd_mult,
            )
            pv = p_val_raw[tid]
            ph = p_hold_raw[tid]
            pv_adj = p_val_holm[tid]
            ph_adj = p_hold_holm[tid]
            holm_sig_both = bool(pv_adj < alpha and ph_adj < alpha)

        entry = {
            "trial_id": tid,
            "category_vi": r["category_vi"],
            "description_vi": r["description_vi"],
            "is_baseline": r["is_baseline"],
            "target_combo_key": r["target_combo_key"],
            "signal_count": r["signal_count"],
            "accepted_signals": r["accepted_signals"],
            "skipped_signals": r["skipped_signals"],
            "rejected_signals": r["rejected_signals"],
            "status_note": r["status_note"],
            "qualifying_dev_regimes": r["qualifying_dev_regimes"],
            "sim_reused_from": r["sim_reused_from"],
            "metrics": m,
            "delta_vs_baseline": {
                "DEVELOPMENT_net_pnl_usd": dev_delta,
                "VALIDATION_net_pnl_usd": val_delta,
                "HOLDOUT_net_pnl_usd": hold_delta,
                "ALL_net_pnl_usd": all_delta,
                "VALIDATION_m2m_dd_usd": round(val_dd - base_val_dd, 4),
                "HOLDOUT_m2m_dd_usd": round(hold_dd - base_hold_dd, 4),
            },
            "stats_test": {
                "val_p_raw": pv,
                "val_p_holm": pv_adj,
                "holdout_p_raw": ph,
                "holdout_p_holm": ph_adj,
                "holm_significant_both": holm_sig_both,
            },
            "is_candidate_improved": improved,
            "equity_curve": r["equity_curve"],
        }
        portfolio_rows.append(entry)

        if improved:
            target = r["target_combo_key"]
            origin_txt = ""
            if target and target in catalog_by_key:
                cat = catalog_by_key[target]
                origin_txt = f" ({cat.symbol}/C{cat.combo_id} · {cat.origin_type})"
            reason = (
                f"Chênh lệch so với danh mục 23 mã cơ sở dương trên cả KIỂM_ĐỊNH ({val_delta:+.2f} USD) "
                f"và KIỂM_CHỨNG_CUỐI ({hold_delta:+.2f} USD), "
                f"sụt giảm M2M tối đa KIỂM_ĐỊNH={val_dd:.2f} USD / KIỂM_CHỨNG_CUỐI={hold_dd:.2f} USD "
                f"(trong ngưỡng {dd_mult:.2f}x cơ sở); "
                f"giá trị p hiệu chỉnh Holm=({pv_adj:.4f}, {ph_adj:.4f}) qua {total_trials_m} phép thử."
            )
            candidate_watchlist.append(
                {
                    "trial_id": tid,
                    "label_vi": tid + origin_txt,
                    "target_combo_key": target,
                    "category_vi": r["category_vi"],
                    "val_net_pnl_usd": m["VALIDATION"]["net_pnl_usd"],
                    "val_delta_usd": val_delta,
                    "val_m2m_dd_usd": val_dd,
                    "holdout_net_pnl_usd": m["HOLDOUT"]["net_pnl_usd"],
                    "holdout_delta_usd": hold_delta,
                    "holdout_m2m_dd_usd": hold_dd,
                    "all_net_pnl_usd": m["ALL"]["net_pnl_usd"],
                    "max_concurrent_positions": m["ALL"]["max_concurrent_positions"],
                    "max_single_combo_share_pct": m["ALL"]["max_single_combo_share_pct"],
                    "val_p_raw": pv,
                    "val_p_holm": pv_adj,
                    "holdout_p_raw": ph,
                    "holdout_p_holm": ph_adj,
                    "holm_significant_both": holm_sig_both,
                    "reason_vi": reason,
                }
            )

    return {
        "baseline_trial_id": baseline["trial_id"],
        "total_trials_m": total_trials_m,
        "family_wise_alpha": alpha,
        "holm_strictest_threshold": round(alpha / total_trials_m, 6) if total_trials_m > 0 else alpha,
        "selection_rule_vi": (
            "Chỉ ghi nhận đề xuất theo dõi khi đồng thời có delta lãi ròng > 0 trên cả KIỂM_ĐỊNH "
            f"và KIỂM_CHỨNG_CUỐI, đồng thời M2M DD không vượt quá {dd_mult:.2f}x danh mục cơ sở 23 mã."
        ),
        "portfolios": portfolio_rows,
        "candidate_watchlist": candidate_watchlist,
    }
