"""Tải dữ liệu Lab 11 chuẩn, đối khớp V3_LAB_11_PRUNE23_BASE (V3-16) và đặc tả danh mục V3_LAB_11."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import datetime_index_to_ms, to_utc_ms
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.reconcile_v3.era_engines import ensure_fixed_v1_registered
from autotrade_v3.research_v3_18.combo_regime_analyzer import trades_to_dataframe
from autotrade_v3.research_v3_18.combos_30 import AUDIT_20_POSITIVE_PAIRS, LIVE23_SOURCE_COMMIT
from autotrade_v3.research_v3_18.regime_features import (
    compute_causal_hourly_regimes,
    load_research_config,
)
from autotrade_v3.research_v3_18b import DEFAULT_CONFIG_PATH
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.sim.engine import SimulationResult, UnifiedSimulator
from autotrade_v3.sim.funding import FundingTable
from autotrade_v3.strategy.base import Signal
from autotrade_v3.strategy.v3_lab_11 import V3Lab11Strategy
from autotrade_v3.strategy.v3_lab_11_prune23 import PRUNE23_EXCLUDED_COMBOS, V3Lab11Prune23Strategy

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FUNDING_HIST_DIR = PROJECT_ROOT / "reports" / "v3_lab_11_funding_3y"
BINGX_CANDLES_1M_DIR = PROJECT_ROOT / "data" / "bingx" / "market" / "candles_1m"
BINGX_FUNDING_DIR = PROJECT_ROOT / "data" / "bingx" / "market" / "funding"
V3_16_BASE_DIR = PROJECT_ROOT / "runs" / "v3_16_all_signals" / "V3_LAB_11_PRUNE23_BASE"

# Ghi chú nâng cấp & hướng giao dịch thực tế trong V3_LAB_11@11.0.0 theo từng cặp (symbol, combo_id)
LAB11_UPGRADE_NOTES: dict[tuple[str, int], tuple[str, str]] = {
    ("BTC-USDT", 1): ("MUA+BÁN", "H2_BUY_VOLUME_1_8 + H2_ATR_1_00 (atr_ratio_50>=1.00)"),
    ("BTC-USDT", 2): ("CHỈ BÁN (H1_SELL_ONLY)", "Loại chiều BUY từ Batch A R1 (V3_LAB_8)"),
    ("BTC-USDT", 3): ("MUA+BÁN (H0 chưa nâng cấp)", "Mã nhân bản từ ETH H0, ngoài live PRUNE23"),
    ("BTC-USDT", 4): ("CHỈ MUA (H1_BUY_ONLY)", "B1_VOL_CONFIRM (vol_ratio_20>=2.20)"),
    ("BTC-USDT", 6): ("MUA+BÁN", "Đặc tả gốc Fixed TP/SL 2.0%/0.67%"),
    ("BTC-USDT", 7): ("CHỈ MUA (H1_BUY_ONLY)", "B1_NO_CHASE_1H (ema_trend_1h_dir<=0.75)"),
    ("BTC-USDT", 9): ("MUA+BÁN", "Đặc tả gốc Fixed TP/SL 1.0%/0.40%"),
    ("BTC-USDT", 10): ("MUA+BÁN", "Đặc tả gốc Fixed TP/SL 1.0%/0.80%"),
    ("BTC-USDT", 11): ("MUA+BÁN", "H1_BUY_0_78 (TIER ADX30)"),
    ("BTC-USDT", 101): ("MUA+BÁN", "NEW6 DeMarker(60) + SMC_STRICT_OB"),
    ("BTC-USDT", 102): ("MUA+BÁN (H0 chưa nâng cấp)", "Mã nhân bản Aroon từ ETH H0, ngoài live PRUNE23"),
    ("BTC-USDT", 103): ("CHỈ BÁN (Bỏ BUY)", "Nhân bản từ ETH + loại BUY theo Batch A (V3_LAB_8)"),
    ("BTC-USDT", 104): ("MUA+BÁN", "H3a_KUMO_BAND_V1 (lọc độ rộng Kumo 1H)"),
    ("BTC-USDT", 105): ("MUA+BÁN (H0 chưa nâng cấp)", "Mã nhân bản KAMA_ER từ ETH H0, ngoài live PRUNE23"),
    ("BTC-USDT", 106): ("MUA+BÁN", "H3_VORTEX_RATIO_1_22 (ratio>=1.22)"),
    ("ETH-USDT", 1): ("MUA+BÁN", "H1_BOTH_1 (slope4h<=1.5, dist4h>=-6.0, CCI<-80)"),
    ("ETH-USDT", 2): ("MUA+BÁN (H0 chưa nâng cấp)", "Mã nhân bản ADX_20 từ BTC H0, ngoài live PRUNE23"),
    ("ETH-USDT", 3): ("MUA+BÁN", "B2_NOT_OVEREXTENDED_4H (ema_trend_4h_dir)"),
    ("ETH-USDT", 4): ("MUA+BÁN", "Đặc tả gốc Fixed TP/SL 2.0%/0.80%"),
    ("ETH-USDT", 6): ("MUA+BÁN", "R2_V4_COMBINED (BUY atr>=1.2, SELL CCI<-100 & atr>=0.9)"),
    ("ETH-USDT", 7): ("MUA+BÁN", "R2_V2_ASYM_BUY_VOL20_SELL_TREND"),
    ("ETH-USDT", 9): ("MUA+BÁN (H0 chưa nâng cấp)", "Mã nhân bản RSI45_FIXST từ BTC H0, ngoài live PRUNE23"),
    ("ETH-USDT", 10): ("MUA+BÁN (BẤT ĐỐI XỨNG)", "H3_ASYMMETRIC (BUY SMC_VETO, SELL SMC_STRICT)"),
    ("ETH-USDT", 11): ("MUA+BÁN (H0 chưa nâng cấp)", "Mã nhân bản TIER từ BTC H0, ngoài live PRUNE23"),
    ("ETH-USDT", 101): ("MUA+BÁN", "H3_B1_DEMARKER (period=60, long>=0.78, short<=0.22)"),
    ("ETH-USDT", 102): ("MUA+BÁN", "V3_ATR065_NO_EXT24H (Aroon)"),
    ("ETH-USDT", 103): ("CHỈ BÁN (Bỏ BUY)", "Bỏ BUY Batch A + lọc biến động B1 (atr_ratio_50)"),
    ("ETH-USDT", 104): ("MUA+BÁN", "A-R2-05_ATR_SL_ADJUSTMENT (Kumo band + SL theo ATR)"),
    ("ETH-USDT", 105): ("CHỈ BÁN (V5)", "V5_SELL_ONLY_ATR140 (bỏ BUY, KAMA_ER + ATR<=1.40)"),
    ("ETH-USDT", 106): ("MUA+BÁN (H0 chưa nâng cấp)", "Mã nhân bản Vortex từ BTC H0, ngoài live PRUNE23"),
}


@dataclass(frozen=True)
class Lab11ComboCatalogRow:
    """Thông tin chuẩn hóa 1 mã trong phiên bản V3_LAB_11@11.0.0."""

    pair_key: str
    combo_symbol: str
    symbol: str
    combo_id: int
    combo_name: str
    timeframe: str
    directions_lab11: str
    entry_variant: str
    entry_params_json: str
    layer2: str
    tp_pct: float
    sl_pct: float
    is_new6: bool
    is_live_23: bool
    live_status_vi: str
    audit_20_status: str
    lab11_upgrade_vi: str
    source_commit: str


def _load_forward_m1(symbol: str) -> pd.DataFrame:
    fwd_path = BINGX_CANDLES_1M_DIR / f"{symbol}.parquet"
    if not fwd_path.is_file():
        return pd.DataFrame()
    raw = pd.read_parquet(fwd_path)
    if raw.empty:
        return pd.DataFrame()
    idx = pd.to_datetime(raw["open_time_ms"].astype("int64"), unit="ms", utc=True)
    df = pd.DataFrame(
        {
            "open": raw["open"].astype(float).to_numpy(),
            "high": raw["high"].astype(float).to_numpy(),
            "low": raw["low"].astype(float).to_numpy(),
            "close": raw["close"].astype(float).to_numpy(),
            "volume": raw["volume"].astype(float).to_numpy(),
            "provider": "BINGX",
        },
        index=idx,
    )
    df.index.name = "timestamp"
    return df


def _resample_m1_to_m15_complete(m1_full: pd.DataFrame) -> pd.DataFrame:
    agg = m1_full[["open", "high", "low", "close", "volume"]].resample("15min").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    counts = m1_full["close"].resample("15min").count()
    return agg.loc[counts == 15].dropna()


def load_merged_funding_table() -> FundingTable:
    """Nạp bảng funding lịch sử 3 năm + forward BingX (chỉ đọc)."""
    dfs: dict[str, pd.DataFrame] = {}
    for sym, prefix in (("BTC-USDT", "BTC"), ("ETH-USDT", "ETH")):
        parts: list[pd.DataFrame] = []
        hist_p = FUNDING_HIST_DIR / f"{prefix}_FUNDING_HISTORY.parquet"
        if hist_p.is_file():
            parts.append(pd.read_parquet(hist_p))
        fwd_p = BINGX_FUNDING_DIR / f"{sym}.parquet"
        if fwd_p.is_file():
            f_raw = pd.read_parquet(fwd_p)
            if not f_raw.empty:
                parts.append(
                    pd.DataFrame(
                        {
                            "funding_time_ms": f_raw["funding_time_ms"].astype("int64"),
                            "funding_rate": f_raw["funding_rate"].astype(float),
                            "mark_price": f_raw["mark_price"].astype(float),
                            "source": "BINGX_COLLECTOR",
                        }
                    )
                )
        if parts:
            merged = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["funding_time_ms"], keep="last")
            dfs[sym] = merged.sort_values("funding_time_ms").reset_index(drop=True)
    return FundingTable.from_dataframes(dfs)


def make_lab11_simulator(
    config: dict[str, Any],
    funding_table: FundingTable | None,
    *,
    enable_funding: bool | None = None,
) -> UnifiedSimulator:
    """Tạo UnifiedSimulator khớp cấu hình run V3_LAB_11_PRUNE23_BASE."""
    sim_cfg = config["simulation"]
    cm = CostModel.from_profile_name(str(sim_cfg["cost_profile"]))
    use_funding = bool(sim_cfg["funding_enabled"]) if enable_funding is None else bool(enable_funding)
    risk = RiskConfig(
        initial_equity=float(sim_cfg["initial_equity"]),
        notional_per_trade=float(sim_cfg["notional_per_trade"]),
    )
    return UnifiedSimulator(
        risk_config=risk,
        cost_model=cm,
        ambiguity_policy=str(sim_cfg["ambiguity_policy"]),
        funding_table=funding_table if use_funding else None,
    )


def build_lab11_combo_catalog(strat30: V3Lab11Strategy | None = None) -> list[Lab11ComboCatalogRow]:
    """Liệt kê đủ 30 mã dưới đặc tả V3_LAB_11@11.0.0 (phân tách rõ 23 mã live và 7 mã ngoài live)."""
    ensure_fixed_v1_registered()
    st = strat30 or V3Lab11Strategy()
    rows: list[Lab11ComboCatalogRow] = []
    for c in sorted(st.spec().combos, key=lambda x: (x.symbol, int(x.combo_id))):
        ep = st.get_exit_policy(c.symbol, int(c.combo_id))
        pair = (c.symbol, int(c.combo_id))
        is_live = pair not in PRUNE23_EXCLUDED_COMBOS
        dirs, upg = LAB11_UPGRADE_NOTES.get(pair, ("MUA+BÁN", "Đặc tả Lab 11"))
        short_sym = c.symbol.split("-")[0]
        rows.append(
            Lab11ComboCatalogRow(
                pair_key=f"{c.symbol}/C{int(c.combo_id)}",
                combo_symbol=f"C{int(c.combo_id)}-{short_sym}",
                symbol=c.symbol,
                combo_id=int(c.combo_id),
                combo_name=c.combo_name,
                timeframe=c.native_timeframe,
                directions_lab11=dirs,
                entry_variant=c.entry_variant,
                entry_params_json=json.dumps(dict(c.entry), sort_keys=True, ensure_ascii=False),
                layer2=c.layer2,
                tp_pct=float(ep.tp_pct),
                sl_pct=float(ep.sl_pct),
                is_new6=bool(c.is_new6),
                is_live_23=is_live,
                live_status_vi="ĐANG_LIVE_23" if is_live else "NGOÀI_LIVE_7",
                audit_20_status="DƯƠNG_20" if pair in AUDIT_20_POSITIVE_PAIRS else "ÂM_10",
                lab11_upgrade_vi=upg,
                source_commit=LIVE23_SOURCE_COMMIT,
            )
        )
    return rows


def load_v3_18b_context(
    *,
    config: dict[str, Any] | None = None,
    data_layer: DataLayer | None = None,
) -> dict[str, Any]:
    """Tải dữ liệu M1 3Y + forward BingX, funding và tín hiệu V3_LAB_11 / PRUNE23."""
    cfg = config or load_research_config(DEFAULT_CONFIG_PATH)
    dl = data_layer or DataLayer(
        catalog_path=PROJECT_ROOT / "catalog" / "research.duckdb",
        qa_path=PROJECT_ROOT / "catalog" / "v3_m1_qa.json",
        data_dir=PROJECT_ROOT / "data" / "m1_raw",
    )
    ds_cfg = cfg["dataset"]
    symbols = list(ds_cfg["symbols"])
    hist_start = str(ds_cfg["hist_start_date"])
    hist_end = str(ds_cfg["hist_end_date"])
    start_ms = to_utc_ms(hist_start)
    hist_end_ms = to_utc_ms(hist_end)

    m1_full_by_sym: dict[str, pd.DataFrame] = {}
    m1_full_eval: dict[str, pd.DataFrame] = {}
    m15_full: dict[str, pd.DataFrame] = {}
    m1_3y_eval: dict[str, pd.DataFrame] = {}
    m15_3y: dict[str, pd.DataFrame] = {}
    hourly_regimes: dict[str, pd.DataFrame] = {}

    for sym in symbols:
        d_hist = dl.query_m1(sym, hist_start, hist_end, include_warmup=True)
        d_fwd = _load_forward_m1(sym)
        cols = [c for c in ("open", "high", "low", "close", "volume", "provider") if c in d_hist.columns or (not d_fwd.empty and c in d_fwd.columns)]
        if not d_fwd.empty:
            comb = pd.concat([d_hist[cols], d_fwd[cols]])
            comb = comb[~comb.index.duplicated(keep="last")].sort_index()
            m1_full_by_sym[sym] = comb
        else:
            m1_full_by_sym[sym] = d_hist[cols]

        d15_cont_3y = dl.query_timeframe(sym, "15m", hist_start, hist_end, include_warmup=True)
        hourly_regimes[sym] = compute_causal_hourly_regimes(d15_cont_3y, config=cfg, enforce_continuity=True)

    full_end_ms = min(int(df.index.asi8.max() // 1_000_000) + 60_000 for df in m1_full_by_sym.values())

    for sym in symbols:
        full_df = m1_full_by_sym[sym]
        t_all = datetime_index_to_ms(full_df.index)
        m1_full_eval[sym] = full_df.loc[(t_all >= start_ms) & (t_all < full_end_ms)]
        m1_3y_eval[sym] = full_df.loc[(t_all >= start_ms) & (t_all < hist_end_ms)]

        m15_all_df = _resample_m1_to_m15_complete(full_df)
        t15 = datetime_index_to_ms(m15_all_df.index)
        m15_full[sym] = m15_all_df.loc[t15 < full_end_ms]
        m15_3y[sym] = m15_all_df.loc[t15 < hist_end_ms]

    funding_table = load_merged_funding_table()

    ensure_fixed_v1_registered()
    strat_all30 = V3Lab11Strategy()
    strat_prune23 = V3Lab11Prune23Strategy()
    catalog = build_lab11_combo_catalog(strat_all30)

    sigs_30_full = [
        s
        for s in strat_all30.generate_signals(m15_full, symbols=symbols, only_approved=True, only_warmup_ready=True)
        if start_ms <= int(s.signal_close_ms) + 1 < full_end_ms
    ]
    sigs_30_full.sort(key=lambda x: (int(x.signal_close_ms), x.symbol, int(x.combo_id), x.side))

    sigs_30_3y = [s for s in sigs_30_full if int(s.signal_close_ms) < hist_end_ms]
    sigs_23_full = [
        s
        for s in strat_prune23.generate_signals(m15_full, symbols=symbols, only_approved=True, only_warmup_ready=True)
        if start_ms <= int(s.signal_close_ms) + 1 < full_end_ms
    ]
    sigs_23_full.sort(key=lambda x: (int(x.signal_close_ms), x.symbol, int(x.combo_id), x.side))
    sigs_23_3y = [s for s in sigs_23_full if int(s.signal_close_ms) < hist_end_ms]

    return {
        "config": cfg,
        "symbols": symbols,
        "start_ms": start_ms,
        "hist_end_ms": hist_end_ms,
        "full_end_ms": full_end_ms,
        "m1_full_eval": m1_full_eval,
        "m15_full": m15_full,
        "m1_3y_eval": m1_3y_eval,
        "m15_3y": m15_3y,
        "hourly_regimes": hourly_regimes,
        "funding_table": funding_table,
        "strat_all30": strat_all30,
        "strat_prune23": strat_prune23,
        "catalog": catalog,
        "sigs_30_full": sigs_30_full,
        "sigs_30_3y": sigs_30_3y,
        "sigs_23_full": sigs_23_full,
        "sigs_23_3y": sigs_23_3y,
    }


def verify_v3_16_baseline_parity(ctx: dict[str, Any]) -> dict[str, Any]:
    """Đối khớp 100% cơ sở V3_LAB_11_PRUNE23@11.0.0 với run V3-16 và giải thích sai khác V3-18."""
    cfg = ctx["config"]
    funding_table: FundingTable = ctx["funding_table"]
    sigs_23_full: list[Signal] = ctx["sigs_23_full"]
    sigs_23_3y: list[Signal] = ctx["sigs_23_3y"]
    m1_full: dict[str, pd.DataFrame] = ctx["m1_full_eval"]
    m1_3y: dict[str, pd.DataFrame] = ctx["m1_3y_eval"]

    manifest_path = V3_16_BASE_DIR / "run_manifest.json"
    dec_path = V3_16_BASE_DIR / "signal_decisions.parquet"
    trades_path = V3_16_BASE_DIR / "trades.parquet"

    ref_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ref_dec = pd.read_parquet(dec_path)
    ref_trades = pd.read_parquet(trades_path)

    sim_bingx = make_lab11_simulator(cfg, funding_table, enable_funding=True)
    res_full: SimulationResult = sim_bingx.run("BINGX_PORTFOLIO", sigs_23_full, m1_full)

    gen_sig_ids = [str(s.signal_id) for s in sigs_23_full]
    dec_sig_ids = [str(d.signal_id) for d in res_full.signal_decisions]
    ref_sig_ids = ref_dec["signal_id"].astype(str).tolist()
    sig_set_match = (len(gen_sig_ids) == len(ref_sig_ids)) and (sorted(gen_sig_ids) == sorted(ref_sig_ids))
    decision_order_match = dec_sig_ids == ref_sig_ids
    sig_match = sig_set_match and decision_order_match

    repro_trades_full = trades_to_dataframe(
        res_full.trades,
        signal_lookup={str(s.signal_id): s for s in sigs_23_full if s.signal_id},
    )
    repro_net_full = float(repro_trades_full["net_pnl"].sum()) if not repro_trades_full.empty else 0.0
    ref_net_full = float(ref_trades["net_pnl"].sum())
    repro_m2m_full = float(res_full.snapshots["drawdown_dollars"].max())
    repro_funding_full = float(res_full.summary_dict.get("funding_net_cashflow", 0.0))

    res_3y_bingx: SimulationResult = sim_bingx.run("BINGX_PORTFOLIO", sigs_23_3y, m1_3y)
    tdf_3y_bingx = trades_to_dataframe(
        res_3y_bingx.trades,
        signal_lookup={str(s.signal_id): s for s in sigs_23_3y if s.signal_id},
    )
    net_3y_bingx = float(tdf_3y_bingx["net_pnl"].sum())
    m2m_3y_bingx = float(res_3y_bingx.snapshots["drawdown_dollars"].max())
    funding_3y = float(res_3y_bingx.summary_dict.get("funding_net_cashflow", 0.0))

    sim_bingx_nofund = make_lab11_simulator(cfg, funding_table, enable_funding=False)
    res_3y_bingx_nofund = sim_bingx_nofund.run("BINGX_PORTFOLIO", sigs_23_3y, m1_3y)
    net_3y_bingx_nofund = sum(float(t.net_pnl) for t in res_3y_bingx_nofund.trades)

    sim_all_nofund = make_lab11_simulator(cfg, funding_table, enable_funding=False)
    res_3y_all_nofund = sim_all_nofund.run("ALL_SIGNALS_PORTFOLIO", sigs_23_3y, m1_3y)
    net_3y_all_nofund = sum(float(t.net_pnl) for t in res_3y_all_nofund.trades)

    net_diff_full = abs(repro_net_full - ref_net_full)
    trades_match = len(repro_trades_full) == int(ref_manifest["total_legs"]) == len(ref_trades)
    parity_ok = bool(sig_set_match and trades_match and net_diff_full < 0.01)

    return {
        "parity_ok": parity_ok,
        "v3_16_reference_dir": str(V3_16_BASE_DIR),
        "v3_16_ref_summary": {
            "total_signals": int(ref_manifest["total_signals"]),
            "accepted_entries": int(ref_manifest["accepted_entries"]),
            "skipped_signals": int(ref_manifest["skipped_signals_count"]),
            "rejected_signals": int(ref_manifest["rejected_signals_count"]),
            "net_pnl_usd": round(ref_net_full, 4),
            "funding_net_cashflow_usd": round(float(ref_manifest["funding_net_cashflow"]), 6),
            "window_end_utc": str(ref_manifest["time_window"]["end"]),
        },
        "reproduced_full_bingx": {
            "total_signals": len(sigs_23_full),
            "exact_signal_id_match": sig_match,
            "exact_signal_set_match": sig_set_match,
            "decision_order_match": decision_order_match,
            "accepted_entries": len(repro_trades_full),
            "skipped_signals": len(res_full.skipped_signals),
            "rejected_signals": len(res_full.rejected_signals),
            "net_pnl_usd": round(repro_net_full, 4),
            "net_pnl_diff_usd": round(net_diff_full, 6),
            "m2m_max_dd_usd": round(repro_m2m_full, 4),
            "funding_net_cashflow_usd": round(repro_funding_full, 6),
        },
        "reconciliation_v3_18_vs_v3_16": {
            "v3_18_all_signals_3y_no_funding": {
                "mode": "ALL_SIGNALS_PORTFOLIO",
                "window": "2023-11-01..2026-11-01",
                "funding": False,
                "signals": len(sigs_23_3y),
                "trades": len(res_3y_all_nofund.trades),
                "net_pnl_usd": round(net_3y_all_nofund, 4),
            },
            "bingx_3y_no_funding": {
                "mode": "BINGX_PORTFOLIO",
                "window": "2023-11-01..2026-11-01",
                "funding": False,
                "signals": len(sigs_23_3y),
                "trades": len(res_3y_bingx_nofund.trades),
                "skipped_signals": len(res_3y_bingx_nofund.skipped_signals),
                "net_pnl_usd": round(net_3y_bingx_nofund, 4),
            },
            "v3_18b_baseline_3y_with_funding": {
                "mode": "BINGX_PORTFOLIO",
                "window": "2023-11-01..2026-11-01",
                "funding": True,
                "signals": len(sigs_23_3y),
                "trades": len(tdf_3y_bingx),
                "skipped_signals": len(res_3y_bingx.skipped_signals),
                "net_pnl_usd": round(net_3y_bingx, 4),
                "m2m_max_dd_usd": round(m2m_3y_bingx, 4),
                "funding_net_cashflow_usd": round(funding_3y, 6),
            },
            "effects_breakdown_usd": {
                "effect_mode_bingx_gating_3y": round(net_3y_bingx_nofund - net_3y_all_nofund, 4),
                "effect_funding_3y": round(net_3y_bingx - net_3y_bingx_nofund, 4),
                "effect_forward_2026_11_01_to_22": round(repro_net_full - net_3y_bingx, 4),
            },
            "explanation_vi": (
                "Chênh lệch giữa số V3-18 (+924,99 USD, 4.247 lệnh) và số run V3-16 V3_LAB_11_PRUNE23_BASE (+593,04 USD, 3.138 lệnh) "
                "đến từ 3 yếu tố đo được chính xác: (1) chuyển từ ALL_SIGNALS_PORTFOLIO sang BINGX_PORTFOLIO (bỏ tín hiệu trùng vị thế cùng cặp/chiều), "
                "(2) cộng chi phí funding lịch sử trong BINGX_PORTFOLIO, và (3) V3-16 bao gồm thêm giai đoạn forward sau 2026-11-01."
            ),
        },
        "baseline_3y_sim_result": res_3y_bingx,
    }
