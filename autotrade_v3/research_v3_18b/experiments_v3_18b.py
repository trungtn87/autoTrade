"""Chạy bảng 23 mã Lab 11 × chế độ và 33 phép thử thêm/bớt/lọc mã trên cơ sở Lab 11."""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import Any

import pandas as pd

from autotrade_v3.research_v3_18.combo_regime_analyzer import (
    SPLIT_ORDER,
    classify_split,
    summarize_trade_slice,
    trades_to_dataframe,
)
from autotrade_v3.research_v3_18.portfolio_experiments import (
    _apply_holm_bonferroni,
    _daily_pnl_series,
    _downsample_equity_curve,
    _paired_daily_bootstrap_pvalue,
    _summarize_portfolio_slice,
    evaluate_portfolio_candidate_flag,
)
from autotrade_v3.research_v3_18.regime_features import (
    ALL_REGIME_DIMENSIONS,
    MARKET_REGIME_ORDER,
    attach_regimes_to_trades_df,
    map_timestamp_to_regime,
)
from autotrade_v3.research_v3_18b.lab11_baseline import Lab11ComboCatalogRow, make_lab11_simulator
from autotrade_v3.sim.engine import SimulationResult
from autotrade_v3.strategy.base import Signal


def _stable_seed(base_seed: int, *parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return (int(base_seed) + int.from_bytes(digest[:4], "little")) % (2**31 - 1)


def filter_signals_by_causal_regime(
    signals: list[Signal],
    hourly_regimes: dict[str, pd.DataFrame],
    *,
    sessions_cfg: dict[str, list[int]],
    exclude_sessions: set[str],
    block_buy_zones: set[str],
    block_sell_zones: set[str],
) -> list[Signal]:
    """Lọc tín hiệu hoàn toàn nhân quả theo phiên UTC và vùng cung/cầu tại `signal_close_ms`."""
    kept: list[Signal] = []
    for s in signals:
        reg = map_timestamp_to_regime(
            hourly_regimes[s.symbol],
            int(s.signal_close_ms),
            sessions_cfg=sessions_cfg,
        )
        if str(reg["phien_giao_dich"]) in exclude_sessions:
            continue
        zone = str(reg["vung_cung_cau_smc"])
        if s.side == "BUY" and zone in block_buy_zones:
            continue
        if s.side == "SELL" and zone in block_sell_zones:
            continue
        kept.append(s)
    return kept


def analyze_lab11_live_23_combos_by_regime(
    *,
    ctx: dict[str, Any],
    baseline_3y_sim: SimulationResult,
) -> dict[str, Any]:
    """Phân tích 23 mã đang live (phiên bản Lab 11, chế độ sổ chung kiểu BingX + funding)."""
    cfg = ctx["config"]
    splits_cfg = cfg["splits"]
    stats_cfg = cfg["statistics"]
    min_n = int(stats_cfg["min_sample_size"])
    insuff = str(stats_cfg["insufficient_sample_label"])
    n_boot = int(stats_cfg["bootstrap_iterations"])
    ci_level = float(stats_cfg["bootstrap_ci_level"])
    base_seed = int(stats_cfg["bootstrap_seed"])

    sigs_23_3y: list[Signal] = ctx["sigs_23_3y"]
    hourly_regimes: dict[str, pd.DataFrame] = ctx["hourly_regimes"]
    catalog: list[Lab11ComboCatalogRow] = ctx["catalog"]
    live23_catalog = [c for c in catalog if c.is_live_23]

    sig_lookup = {str(s.signal_id): s for s in sigs_23_3y if s.signal_id}
    raw_df = trades_to_dataframe(baseline_3y_sim.trades, signal_lookup=sig_lookup)
    annotated = attach_regimes_to_trades_df(raw_df, hourly_regimes, config=cfg)
    annotated["split"] = [classify_split(int(ts), splits_cfg) for ts in annotated["signal_close_ms"].tolist()]

    regime_keys = list(MARKET_REGIME_ORDER)
    extra = sorted(set(annotated["che_do_thi_truong"].astype(str).unique()).difference(regime_keys))
    regime_keys.extend(extra)

    per_combo: list[dict[str, Any]] = []
    for item in live23_catalog:
        c_df = annotated.loc[
            (annotated["symbol"] == item.symbol) & (annotated["combo_id"].astype(int) == int(item.combo_id))
        ]
        overall_by_split: dict[str, Any] = {}
        for sp_name in ("ALL",) + SPLIT_ORDER:
            sp_df = c_df if sp_name == "ALL" else c_df.loc[c_df["split"] == sp_name]
            overall_by_split[sp_name] = summarize_trade_slice(
                sp_df,
                min_sample_size=min_n,
                insufficient_label=insuff,
                n_boot=n_boot,
                ci_level=ci_level,
                seed=_stable_seed(base_seed, item.pair_key, "OVERALL", sp_name),
            )

        by_regime: dict[str, Any] = {}
        by_regime_splits: dict[str, dict[str, Any]] = {sp: {} for sp in SPLIT_ORDER}
        for reg_name in regime_keys:
            r_df = c_df.loc[c_df["che_do_thi_truong"] == reg_name]
            if r_df.empty and reg_name not in MARKET_REGIME_ORDER:
                continue
            by_regime[reg_name] = summarize_trade_slice(
                r_df,
                min_sample_size=min_n,
                insufficient_label=insuff,
                n_boot=n_boot,
                ci_level=ci_level,
                seed=_stable_seed(base_seed, item.pair_key, reg_name, "ALL"),
            )
            for sp_name in SPLIT_ORDER:
                by_regime_splits[sp_name][reg_name] = summarize_trade_slice(
                    r_df.loc[r_df["split"] == sp_name],
                    min_sample_size=min_n,
                    insufficient_label=insuff,
                    n_boot=n_boot,
                    ci_level=ci_level,
                    seed=_stable_seed(base_seed, item.pair_key, reg_name, sp_name),
                )

        per_combo.append(
            {
                "catalog": asdict(item),
                "overall_all": overall_by_split["ALL"],
                "overall_by_split": {sp: overall_by_split[sp] for sp in SPLIT_ORDER},
                "by_regime": by_regime,
                "by_regime_splits": by_regime_splits,
            }
        )

    dim_summaries: dict[str, list[dict[str, Any]]] = {}
    for dim in ALL_REGIME_DIMENSIONS:
        vals = list(MARKET_REGIME_ORDER) if dim == "che_do_thi_truong" else sorted(annotated[dim].astype(str).unique())
        rows_dim: list[dict[str, Any]] = []
        for v in sorted(
            set(vals).union(set(annotated[dim].astype(str).unique())),
            key=lambda x: (vals.index(x) if x in vals else 999, x),
        ):
            v_df = annotated.loc[annotated[dim] == v]
            entry: dict[str, Any] = {
                "dimension": dim,
                "label": v,
                "all": summarize_trade_slice(
                    v_df,
                    min_sample_size=min_n,
                    insufficient_label=insuff,
                    n_boot=n_boot,
                    ci_level=ci_level,
                    seed=_stable_seed(base_seed, "DIM", dim, v, "ALL"),
                ),
            }
            for sp_name in SPLIT_ORDER:
                entry[sp_name] = summarize_trade_slice(
                    v_df.loc[v_df["split"] == sp_name],
                    min_sample_size=min_n,
                    insufficient_label=insuff,
                    n_boot=n_boot,
                    ci_level=ci_level,
                    seed=_stable_seed(base_seed, "DIM", dim, v, sp_name),
                )
            rows_dim.append(entry)
        dim_summaries[dim] = rows_dim

    return {
        "regime_order": list(MARKET_REGIME_ORDER) + [r for r in extra if not annotated.loc[annotated["che_do_thi_truong"] == r].empty],
        "total_accepted_trades_3y": int(len(annotated)),
        "total_signals_3y": len(sigs_23_3y),
        "per_combo": per_combo,
        "dimension_summaries": dim_summaries,
        "annotated_trades": annotated,
    }


def _trial_display_code(symbol: str, combo_id: int) -> str:
    prefix = symbol.split("-")[0]
    return f"C{int(combo_id)}_{prefix}"


def run_v3_18b_portfolio_experiments(
    *,
    ctx: dict[str, Any],
    baseline_3y_sim: SimulationResult,
) -> dict[str, Any]:
    """Chạy đồng nhất 33 phép thử (7 thêm + 23 bớt + 3 lọc) trên cơ sở Lab 11 kiểu BingX."""
    cfg = ctx["config"]
    sim_cfg = cfg["simulation"]
    splits_cfg = cfg["splits"]
    stats_cfg = cfg["statistics"]
    rules_cfg = cfg["portfolio_selection_rules"]

    exec_mode = str(sim_cfg["execution_mode"])
    initial_equity = float(sim_cfg["initial_equity"])
    n_boot = int(stats_cfg["bootstrap_iterations"])
    base_seed = int(stats_cfg["bootstrap_seed"])
    alpha = float(stats_cfg["family_wise_alpha"])
    dd_mult = float(rules_cfg["max_dd_multiplier_vs_baseline"])

    m1_3y: dict[str, pd.DataFrame] = ctx["m1_3y_eval"]
    hourly_regimes: dict[str, pd.DataFrame] = ctx["hourly_regimes"]
    funding_table = ctx["funding_table"]
    sigs_23_3y: list[Signal] = ctx["sigs_23_3y"]
    sigs_30_3y: list[Signal] = ctx["sigs_30_3y"]
    catalog: list[Lab11ComboCatalogRow] = ctx["catalog"]

    live23_rows = sorted([c for c in catalog if c.is_live_23], key=lambda x: (x.symbol, int(x.combo_id)))
    non_live7_rows = sorted([c for c in catalog if not c.is_live_23], key=lambda x: (x.symbol, int(x.combo_id)))

    specs: list[dict[str, Any]] = [
        {
            "trial_id": "LAB11_PRUNE23_CO_SO",
            "group_type": "CO_SO",
            "group_label_vi": "Cơ sở Lab 11 (23 mã)",
            "target_combo_symbol": None,
            "target_pair_key": None,
            "description_vi": "Danh mục chuẩn V3_LAB_11_PRUNE23@11.0.0 chạy sổ chung kiểu BingX + funding (2 bps/chiều).",
            "is_baseline": True,
            "signals": sigs_23_3y,
            "build_status_vi": "DỰNG_CHUẨN_LAB11",
            "pre_cached_sim": baseline_3y_sim,
        }
    ]

    # 7 phép thử THÊM MÃ
    for item in non_live7_rows:
        added_sigs = [s for s in sigs_30_3y if s.symbol == item.symbol and int(s.combo_id) == int(item.combo_id)]
        code = _trial_display_code(item.symbol, item.combo_id)
        if not added_sigs:
            combined_sigs = sigs_23_3y
            status_vi = "KHÔNG_DỰNG_ĐƯỢC_PHIÊN_BẢN_LAB11"
        else:
            combined_sigs = sorted(
                sigs_23_3y + added_sigs,
                key=lambda x: (int(x.signal_close_ms), x.symbol, int(x.combo_id), x.side),
            )
            status_vi = "DỰNG_ĐƯỢC_LAB11_30MÃ"
        specs.append(
            {
                "trial_id": f"THEM_{code}",
                "group_type": "THEM_7",
                "group_label_vi": "Thêm 1 mã ngoài live",
                "target_combo_symbol": item.combo_symbol,
                "target_pair_key": item.pair_key,
                "description_vi": (
                    f"Cơ sở Lab 11 + {item.pair_key} ({item.combo_symbol}, {len(added_sigs)} tín hiệu · {item.lab11_upgrade_vi})."
                ),
                "is_baseline": False,
                "signals": combined_sigs,
                "build_status_vi": status_vi,
                "added_combo_signals": len(added_sigs),
                "pre_cached_sim": None,
            }
        )

    # 23 phép thử BỚT MÃ (leave-one-out)
    for item in live23_rows:
        code = _trial_display_code(item.symbol, item.combo_id)
        rem_sigs = [
            s for s in sigs_23_3y if not (s.symbol == item.symbol and int(s.combo_id) == int(item.combo_id))
        ]
        specs.append(
            {
                "trial_id": f"BOT_{code}",
                "group_type": "BOT_23",
                "group_label_vi": "Bớt 1 mã đang live",
                "target_combo_symbol": item.combo_symbol,
                "target_pair_key": item.pair_key,
                "description_vi": f"Cơ sở Lab 11 bỏ {item.pair_key} ({item.combo_symbol}, {item.lab11_upgrade_vi}).",
                "is_baseline": False,
                "signals": rem_sigs,
                "build_status_vi": "BO_1_MA_KHOI_LAB11",
                "added_combo_signals": 0,
                "pre_cached_sim": None,
            }
        )

    # 3 phép thử LỌC PHIÊN / VÙNG CUNG-CẦU
    sessions_cfg = dict(cfg["regime_params"]["sessions_utc"])
    for flt in cfg["pre_registered_filters"]:
        fid = str(flt["filter_id"])
        filt_sigs = filter_signals_by_causal_regime(
            sigs_23_3y,
            hourly_regimes,
            sessions_cfg=sessions_cfg,
            exclude_sessions=set(flt["exclude_sessions_utc"]),
            block_buy_zones=set(flt["block_buy_zones"]),
            block_sell_zones=set(flt["block_sell_zones"]),
        )
        specs.append(
            {
                "trial_id": fid,
                "group_type": "LOC_3",
                "group_label_vi": "Lọc phiên / cung-cầu nhân quả",
                "target_combo_symbol": None,
                "target_pair_key": None,
                "description_vi": f"{flt['description_vi']} (giữ {len(filt_sigs)}/{len(sigs_23_3y)} tín hiệu).",
                "is_baseline": False,
                "signals": filt_sigs,
                "build_status_vi": "LOC_NHAN_QUA",
                "added_combo_signals": 0,
                "pre_cached_sim": None,
            }
        )

    sim = make_lab11_simulator(cfg, funding_table, enable_funding=True)
    evaluated: list[dict[str, Any]] = []
    total_specs = len(specs)

    for idx, sp in enumerate(specs, 1):
        sigs: list[Signal] = sp["signals"]
        sim_res: SimulationResult = (
            sp["pre_cached_sim"]
            if sp["pre_cached_sim"] is not None
            else sim.run(exec_mode, sigs, m1_3y)
        )
        sig_lookup = {str(s.signal_id): s for s in sigs if s.signal_id}
        tdf = trades_to_dataframe(sim_res.trades, signal_lookup=sig_lookup)
        if not tdf.empty:
            tdf["split"] = [classify_split(int(ts), splits_cfg) for ts in tdf["signal_close_ms"].tolist()]
        else:
            tdf["split"] = pd.Series(dtype=str)

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

        evaluated.append(
            {
                "trial_id": sp["trial_id"],
                "group_type": sp["group_type"],
                "group_label_vi": sp["group_label_vi"],
                "target_combo_symbol": sp["target_combo_symbol"],
                "target_pair_key": sp["target_pair_key"],
                "description_vi": sp["description_vi"],
                "is_baseline": bool(sp["is_baseline"]),
                "build_status_vi": sp["build_status_vi"],
                "signal_count": len(sigs),
                "accepted_signals": len(sim_res.accepted_signals),
                "skipped_signals": len(sim_res.skipped_signals),
                "rejected_signals": len(sim_res.rejected_signals),
                "funding_net_cashflow_usd": round(float(sim_res.summary_dict.get("funding_net_cashflow", 0.0)), 6),
                "metrics": split_stats,
                "equity_curve": _downsample_equity_curve(sim_res.snapshots),
                "_trades_df": tdf,
            }
        )
        if idx == 1 or idx % 5 == 0 or idx == total_specs:
            print(f"[V3-18b] Đã chạy {idx}/{total_specs}: {sp['trial_id']} (net={split_stats['ALL']['net_pnl_usd']:+.2f})", flush=True)

    baseline = next(r for r in evaluated if r["is_baseline"])
    base_tdf: pd.DataFrame = baseline["_trades_df"]
    base_val_daily = _daily_pnl_series(base_tdf, "VALIDATION", splits_cfg)
    base_hold_daily = _daily_pnl_series(base_tdf, "HOLDOUT", splits_cfg)
    base_dev_net = float(baseline["metrics"]["DEVELOPMENT"]["net_pnl_usd"])
    base_val_net = float(baseline["metrics"]["VALIDATION"]["net_pnl_usd"])
    base_hold_net = float(baseline["metrics"]["HOLDOUT"]["net_pnl_usd"])
    base_all_net = float(baseline["metrics"]["ALL"]["net_pnl_usd"])
    base_val_dd = float(baseline["metrics"]["VALIDATION"]["m2m_max_dd_usd"])
    base_hold_dd = float(baseline["metrics"]["HOLDOUT"]["m2m_max_dd_usd"])

    non_baseline = [r for r in evaluated if not r["is_baseline"]]
    p_val_raw: dict[str, float] = {}
    p_hold_raw: dict[str, float] = {}
    for r in non_baseline:
        tid = str(r["trial_id"])
        tdf = r["_trades_df"]
        p_val_raw[tid] = _paired_daily_bootstrap_pvalue(
            _daily_pnl_series(tdf, "VALIDATION", splits_cfg),
            base_val_daily,
            n_boot=n_boot,
            seed=_stable_seed(base_seed, "PAIR", tid, "VALIDATION"),
        )
        p_hold_raw[tid] = _paired_daily_bootstrap_pvalue(
            _daily_pnl_series(tdf, "HOLDOUT", splits_cfg),
            base_hold_daily,
            n_boot=n_boot,
            seed=_stable_seed(base_seed, "PAIR", tid, "HOLDOUT"),
        )

    p_val_holm = _apply_holm_bonferroni(p_val_raw)
    p_hold_holm = _apply_holm_bonferroni(p_hold_raw)
    total_trials_m = len(non_baseline)

    rows_out: list[dict[str, Any]] = []
    candidate_watchlist: list[dict[str, Any]] = []

    for r in evaluated:
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
            pv, ph, pv_adj, ph_adj, holm_both = None, None, None, None, False
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
            holm_both = bool(pv_adj < alpha and ph_adj < alpha)

        entry = {
            "trial_id": tid,
            "group_type": r["group_type"],
            "group_label_vi": r["group_label_vi"],
            "target_combo_symbol": r["target_combo_symbol"],
            "target_pair_key": r["target_pair_key"],
            "description_vi": r["description_vi"],
            "is_baseline": r["is_baseline"],
            "build_status_vi": r["build_status_vi"],
            "signal_count": r["signal_count"],
            "accepted_signals": r["accepted_signals"],
            "skipped_signals": r["skipped_signals"],
            "rejected_signals": r["rejected_signals"],
            "funding_net_cashflow_usd": r["funding_net_cashflow_usd"],
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
                "holm_significant_both": holm_both,
            },
            "is_candidate_improved": improved,
            "equity_curve": r["equity_curve"],
        }
        rows_out.append(entry)

        if improved:
            candidate_watchlist.append(
                {
                    "trial_id": tid,
                    "group_type": r["group_type"],
                    "group_label_vi": r["group_label_vi"],
                    "target_combo_symbol": r["target_combo_symbol"],
                    "target_pair_key": r["target_pair_key"],
                    "val_net_pnl_usd": m["VALIDATION"]["net_pnl_usd"],
                    "val_delta_usd": val_delta,
                    "val_m2m_dd_usd": val_dd,
                    "holdout_net_pnl_usd": m["HOLDOUT"]["net_pnl_usd"],
                    "holdout_delta_usd": hold_delta,
                    "holdout_m2m_dd_usd": hold_dd,
                    "all_net_pnl_usd": m["ALL"]["net_pnl_usd"],
                    "all_delta_usd": all_delta,
                    "max_concurrent_positions": m["ALL"]["max_concurrent_positions"],
                    "max_single_combo_share_pct": m["ALL"]["max_single_combo_share_pct"],
                    "val_p_raw": pv,
                    "val_p_holm": pv_adj,
                    "holdout_p_raw": ph,
                    "holdout_p_holm": ph_adj,
                    "holm_significant_both": holm_both,
                    "reason_vi": (
                        f"Vượt cơ sở Lab 11 trên cả KIỂM_ĐỊNH ({val_delta:+.2f} USD) và KIỂM_CHỨNG_CUỐI ({hold_delta:+.2f} USD), "
                        f"sụt giảm M2M tối đa ở ngưỡng <= {dd_mult:.2f}x cơ sở; "
                        f"giá trị p hiệu chỉnh Holm qua {total_trials_m} phép thử là ({pv_adj:.4f}, {ph_adj:.4f}) "
                        "(lưu ý dữ liệu 3 năm này cũng là mẫu đã dùng chọn Lab 11)."
                    ),
                }
            )

    counts_by_group = {
        "add_7": sum(1 for r in non_baseline if r["group_type"] == "THEM_7"),
        "drop_23": sum(1 for r in non_baseline if r["group_type"] == "BOT_23"),
        "filter_3": sum(1 for r in non_baseline if r["group_type"] == "LOC_3"),
    }

    return {
        "baseline_trial_id": "LAB11_PRUNE23_CO_SO",
        "execution_mode": exec_mode,
        "funding_enabled": True,
        "total_trials_m": total_trials_m,
        "counts_by_group": counts_by_group,
        "family_wise_alpha": alpha,
        "holm_strictest_threshold": round(alpha / total_trials_m, 6) if total_trials_m > 0 else alpha,
        "selection_bias_warning_vi": (
            "CẢNH BÁO THIÊN LỆCH CHỌN MẪU: 23 mã Lab 11 do Owner chọn sau khi đã quan sát dữ liệu 3 năm 2023–2026; "
            "do đó kết quả trên KIỂM_ĐỊNH và KIỂM_CHỨNG_CUỐI của Lab 11 chỉ mang tính đối chiếu nội bộ chứ không phải ngoài mẫu độc lập hoàn toàn."
        ),
        "trials": rows_out,
        "candidate_watchlist": candidate_watchlist,
    }
