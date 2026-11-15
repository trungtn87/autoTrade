"""Single source of truth for Run Detail UI and AI analysis bundles."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any

import pandas as pd


SLIPPAGE_LEVELS = (0.0, 0.02, 0.035, 0.05)
GUARDRAILS = (
    "Cảnh báo 1: Các combo chạy chung vị thế trên sàn (BINGX_PORTFOLIO), do đó phải đánh giá thực lực "
    "độc lập qua pnl_own_entry song song với net_pnl. Không được phép lấy tổng PnL trừ đi combo âm một cách cơ học.",
    "Cảnh báo 2: Cột signals là số tín hiệu ĐÃ QUA bộ lọc kỹ thuật. Tỷ lệ entries / signals < 100% phản ánh "
    "việc từ chối vào lệnh do đang có vị thế mở cùng cặp (ACTIVE_POSITION_LOCK), không phản ánh độ chặt của bộ lọc.",
)


@dataclass(frozen=True)
class AIAnalysisBundle:
    payload: dict[str, Any]
    markdown: str
    json_text: str


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _notional(frame: pd.DataFrame) -> pd.Series:
    if "notional" in frame.columns:
        return frame["notional"].fillna(0.0).astype(float)
    if {"qty", "entry_price"}.issubset(frame.columns):
        return frame["qty"].fillna(0.0).astype(float).abs() * frame["entry_price"].fillna(0.0).astype(float).abs()
    return pd.Series(0.0, index=frame.index, dtype="float64")


def _weighted_pf(frame: pd.DataFrame) -> tuple[float, float, float]:
    if frame.empty:
        return 0.0, 0.0, 0.0
    trade_net = frame.groupby("trade_id")["net_pnl"].sum().astype(float)
    gross_profit = float(trade_net[trade_net > 0].sum())
    gross_loss = float(trade_net[trade_net < 0].sum())
    weighted_pf = gross_profit / abs(gross_loss) if gross_loss < -1e-12 else (999.0 if gross_profit > 0 else 0.0)
    return gross_profit, gross_loss, weighted_pf


def _aggregate(frame: pd.DataFrame, signals: int, label: str) -> dict[str, Any]:
    gross_profit, gross_loss, weighted_pf = _weighted_pf(frame)
    entries = int(frame["trade_id"].nunique()) if not frame.empty else 0
    own = float(frame["pnl_own_entry"].astype(float).sum()) if "pnl_own_entry" in frame.columns else float(frame["net_pnl"].astype(float).sum())
    net = float(frame["net_pnl"].astype(float).sum()) if not frame.empty else 0.0
    fees = float(frame["fee_total"].astype(float).sum()) if not frame.empty else 0.0
    notional = float(_notional(frame).sum()) if not frame.empty else 0.0
    return {
        "group": str(label),
        "signals": int(signals),
        "entries": entries,
        "pos_fill_rate_pct": entries / signals * 100.0 if signals else 0.0,
        "fees": fees,
        "pnl_own_entry": own,
        "net_pnl": net,
        "attribution_delta": net - own,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "weighted_pf": weighted_pf,
        "total_notional_volume": notional,
        "net_pnl_per_entry": net / entries if entries else 0.0,
        "break_even_slippage_per_side_pct": net / (2.0 * notional) * 100.0 if notional else 0.0,
    }


def _enrich_ledgers(
    legs_df: pd.DataFrame,
    decisions_df: pd.DataFrame,
    strategy_combos: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    config_columns = [
        "combo_key", "symbol", "combo_name", "timeframe", "entry_variant", "entry", "filters", "tp_pct", "sl_pct",
        "trailing_act_pct", "trailing_cb_pct", "leg_weights",
    ]
    config = strategy_combos.copy()
    for column in config_columns:
        if column not in config.columns:
            config[column] = "" if column in {"combo_key", "symbol", "combo_name", "timeframe", "entry_variant", "entry", "filters", "leg_weights"} else 0.0
    config = config[config_columns].drop_duplicates("combo_key")
    config["entry"] = config["entry"].fillna("").astype(str)
    config["filters"] = config["filters"].fillna("").astype(str)
    config["leg_weights"] = config["leg_weights"].fillna("[1.0]").astype(str)
    config["leg_structure"] = config["leg_weights"].map(lambda value: "2-Leg" if "," in value else "1-Leg")
    config["rr_ratio"] = config.apply(
        lambda row: _safe_float(row["tp_pct"]) / _safe_float(row["sl_pct"]) if _safe_float(row["sl_pct"]) else 0.0,
        axis=1,
    )

    legs = legs_df.copy()
    legs["combo_key"] = legs["symbol"].astype(str) + "/" + legs["combo_name"].astype(str)
    if "pnl_own_entry" not in legs.columns:
        legs["pnl_own_entry"] = legs["net_pnl"].astype(float)
    legs = legs.merge(
        config[["combo_key", "entry_variant", "entry", "filters", "leg_weights", "leg_structure", "rr_ratio"]],
        on="combo_key",
        how="left",
        validate="many_to_one",
    )

    decisions = decisions_df.copy()
    decisions["combo_key"] = decisions["symbol"].astype(str) + "/" + decisions["combo_name"].astype(str)
    decisions = decisions.merge(
        config[["combo_key", "filters", "leg_weights", "leg_structure"]],
        on="combo_key",
        how="left",
        validate="many_to_one",
    )
    for frame in (legs, decisions):
        for column in ("entry_variant", "entry", "filters", "leg_weights", "leg_structure"):
            if column in frame.columns:
                frame[column] = frame[column].fillna("").astype(str)
    return legs, decisions, config


def _group_breakdown(legs: pd.DataFrame, decisions: pd.DataFrame, column: str, title: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    values = sorted({str(value) for value in legs[column].dropna().tolist()})
    for value in values:
        frame = legs[legs[column].astype(str) == value]
        signals = int((decisions[column].astype(str) == value).sum()) if column in decisions.columns else int(frame["signal_id"].nunique())
        row = _aggregate(frame, signals, value)
        row["dimension"] = title
        rows.append(row)
    return rows


def _sharpe_ratio(equity_df: pd.DataFrame) -> float:
    if equity_df.empty or "equity" not in equity_df.columns:
        return 0.0
    frame = equity_df.copy()
    if "timestamp_dt" not in frame.columns and "time_ms" in frame.columns:
        frame["timestamp_dt"] = pd.to_datetime(frame["time_ms"].astype("int64"), unit="ms", utc=True)
    if "timestamp_dt" not in frame.columns:
        return 0.0
    daily = frame.sort_values("timestamp_dt").set_index("timestamp_dt")["equity"].astype(float).resample("1D").last().ffill()
    returns = daily.pct_change().dropna()
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    return float((returns.mean() / std) * math.sqrt(365.0)) if std > 1e-12 else 0.0


def build_run_report(
    summary: dict[str, Any],
    legs_df: pd.DataFrame,
    decisions_df: pd.DataFrame,
    equity_df: pd.DataFrame,
    strategy_combos: pd.DataFrame,
    cost_breakdown: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the complete normalized report consumed by both UI and exporter."""
    legs, decisions, config = _enrich_ledgers(legs_df, decisions_df, strategy_combos)
    overall = _aggregate(legs, len(decisions), "Overall")
    initial_capital = _safe_float(summary.get("initial_equity"), 0.0)
    max_dd_dollars = _safe_float(summary.get("m2m_max_drawdown_dollars"), _safe_float(summary.get("closed_trade_max_drawdown_dollars")))
    max_dd_pct = _safe_float(summary.get("m2m_max_drawdown_pct"), _safe_float(summary.get("closed_trade_max_drawdown_pct")))
    overall.update(
        initial_capital=initial_capital,
        ending_balance=initial_capital + overall["net_pnl"],
        roi_pct=overall["net_pnl"] / initial_capital * 100.0 if initial_capital else 0.0,
        fee_drag_pct=overall["fees"] / _safe_float(summary.get("gross_pnl")) * 100.0 if abs(_safe_float(summary.get("gross_pnl"))) > 1e-12 else 0.0,
        max_drawdown_dollars=max_dd_dollars,
        max_drawdown_pct=max_dd_pct,
        recovery_factor=overall["net_pnl"] / max_dd_dollars if max_dd_dollars else 0.0,
        sharpe_ratio=_sharpe_ratio(equity_df),
        leg_win_rate_pct=_safe_float(summary.get("leg_win_rate_pct")),
    )

    trade_net = legs.groupby("trade_id", as_index=False).agg(net_pnl=("net_pnl", "sum"), side=("side", "first"))
    side_stats: dict[str, dict[str, Any]] = {}
    for side, group in trade_net.groupby("side", sort=True):
        side_stats[str(side)] = {
            "trades": len(group),
            "win_rate_pct": float((group["net_pnl"].astype(float) > 0).mean() * 100.0) if len(group) else 0.0,
        }

    breakdowns = {
        "Overall": [overall],
        "Symbol": _group_breakdown(legs, decisions, "symbol", "Symbol"),
        "Leg Structure": _group_breakdown(legs, decisions, "leg_structure", "Leg Structure"),
        "Timeframe": _group_breakdown(legs, decisions, "timeframe", "Timeframe"),
        "Filter": _group_breakdown(legs, decisions, "filters", "Filter"),
    }

    combo_rows: list[dict[str, Any]] = []
    signal_counts = decisions["combo_key"].value_counts().to_dict()
    for combo_key, frame in legs.groupby("combo_key", sort=True):
        aggregate = _aggregate(frame, int(signal_counts.get(combo_key, 0)), combo_key)
        cfg_rows = config[config["combo_key"] == combo_key]
        cfg = cfg_rows.iloc[0].to_dict() if not cfg_rows.empty else {}
        aggregate.update(
            combo_key=combo_key,
            symbol=str(frame["symbol"].iloc[0]),
            timeframe=str(frame["timeframe"].iloc[0]),
            leg_structure=str(cfg.get("leg_structure", "")),
            leg_weights=str(cfg.get("leg_weights", "")),
            entry_variant=str(cfg.get("entry_variant", "")),
            entry=str(cfg.get("entry", "")),
            filters=str(cfg.get("filters", "")),
            tp_pct=_safe_float(cfg.get("tp_pct")),
            sl_pct=_safe_float(cfg.get("sl_pct")),
            trailing_act_pct=_safe_float(cfg.get("trailing_act_pct")),
            trailing_cb_pct=_safe_float(cfg.get("trailing_cb_pct")),
            rr_ratio=_safe_float(cfg.get("rr_ratio")),
        )
        combo_rows.append(aggregate)
    combo_table = pd.DataFrame(combo_rows).sort_values("pnl_own_entry", ascending=False, kind="mergesort").reset_index(drop=True)

    costs = cost_breakdown or {}
    metadata = {
        "run_id": str(summary.get("run_id", "")),
        "strategy_ref": f"{summary.get('strategy_id', '')}@{summary.get('strategy_version', '')}",
        "time_window": summary.get("time_window", {}),
        "execution_mode": str(summary.get("execution_mode", "")),
        "fee_mode": str(summary.get("cost_profile", "")),
        "slippage": _safe_float(costs.get("slippage_cost")),
        "funding": _safe_float(costs.get("funding_cost")),
        "slippage_cost": _safe_float(costs.get("slippage_cost")),
        "funding_cost": _safe_float(costs.get("funding_cost")),
        "validation_status": str(summary.get("validation_status", "UNVERIFIED")),
        "dataset_hash": str(summary.get("dataset_hash", "")),
    }
    warnings: list[str] = []
    if metadata["fee_mode"].upper() == "IDEAL":
        warnings.append("FEE MODE IDEAL: kết quả chưa bao gồm slippage/funding thực tế.")
    if overall["break_even_slippage_per_side_pct"] < 0.05:
        warnings.append("Break-even slippage dưới 0.05%/side: lợi thế rất nhạy với chi phí khớp lệnh.")
    metadata["auto_warnings"] = warnings
    return {
        "metadata": metadata,
        "portfolio": overall,
        "side_stats": side_stats,
        "breakdowns": breakdowns,
        "combo_table": combo_table,
    }


def simulate_cost_sensitivity(
    report: dict[str, Any],
    slippage_levels: tuple[float, ...] = SLIPPAGE_LEVELS,
    *,
    extra_fee_pct: float = 0.075,
) -> dict[str, Any]:
    combo = report["combo_table"].copy()
    portfolio_rows: list[dict[str, Any]] = []
    combo_rows: list[dict[str, Any]] = []
    for mode, fee_pct in (("IDEAL", 0.0), ("REALISTIC_TAKER_FUNDING", extra_fee_pct)):
        for slippage in slippage_levels:
            adjusted = combo["net_pnl"].astype(float) - combo["total_notional_volume"].astype(float) * ((2.0 * slippage + fee_pct) / 100.0)
            portfolio_rows.append({"fee_mode": mode, "slippage_pct": slippage, "net_pnl": float(adjusted.sum())})
            for index, value in adjusted.items():
                combo_rows.append({
                    "fee_mode": mode,
                    "slippage_pct": slippage,
                    "combo_key": str(combo.loc[index, "combo_key"]),
                    "base_net_pnl": float(combo.loc[index, "net_pnl"]),
                    "adjusted_net_pnl": float(value),
                })
    combos = pd.DataFrame(combo_rows)
    transitioned = combos[
        (combos["slippage_pct"] >= 0.02)
        & (combos["base_net_pnl"] > 0)
        & (combos["adjusted_net_pnl"] <= 0)
    ].sort_values(["combo_key", "fee_mode", "slippage_pct"], kind="mergesort").drop_duplicates(["combo_key", "fee_mode"])
    return {
        "portfolio": pd.DataFrame(portfolio_rows),
        "combos": combos,
        "profit_to_loss_combos": transitioned.reset_index(drop=True),
        "extra_fee_pct": extra_fee_pct,
    }


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_clean(child) for child in value]
    if isinstance(value, tuple):
        return [_clean(child) for child in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if pd.isna(value) if not isinstance(value, (str, bytes, bool)) else False:
        return ""
    return value.item() if hasattr(value, "item") else value


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return _clean(frame.to_dict(orient="records"))


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No data_"
    columns = list(rows[0])
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(str(row.get(column, "")).replace("|", "\\|") for column in columns) + " |" for row in rows]
    return "\n".join([header, divider, *body])


def build_ai_analysis_bundle(report: dict[str, Any], sensitivity: dict[str, Any]) -> AIAnalysisBundle:
    breakdown_rows: list[dict[str, Any]] = []
    for dimension, rows in report["breakdowns"].items():
        for row in rows:
            breakdown_rows.append({"dimension": dimension, **row})
    payload = {
        "1_RUN_METADATA_AND_ALERTS": _clean(report["metadata"]),
        "2_PORTFOLIO_AND_GROUP_BREAKDOWN": _clean(breakdown_rows),
        "3_COST_SENSITIVITY_MATRIX": {
            "portfolio": _records(sensitivity["portfolio"]),
            "profit_to_loss_combos": _records(sensitivity["profit_to_loss_combos"]),
            "extra_fee_pct": float(sensitivity["extra_fee_pct"]),
        },
        "4_UNIFIED_COMBO_TABLE": _records(report["combo_table"]),
        "5_ANALYTICAL_GUARDRAILS": list(GUARDRAILS),
    }
    payload = _clean(payload)
    json_text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    sections = [
        "# AutoTrade V3 — AI Analysis Bundle",
        "## 1_RUN_METADATA_AND_ALERTS\n\n```json\n" + json.dumps(payload["1_RUN_METADATA_AND_ALERTS"], ensure_ascii=False, indent=2, allow_nan=False) + "\n```",
        "## 2_PORTFOLIO_AND_GROUP_BREAKDOWN\n\n" + _markdown_table(payload["2_PORTFOLIO_AND_GROUP_BREAKDOWN"]),
        "## 3_COST_SENSITIVITY_MATRIX\n\n### Portfolio\n\n" + _markdown_table(payload["3_COST_SENSITIVITY_MATRIX"]["portfolio"]) + "\n\n### Profit-to-loss combos\n\n" + _markdown_table(payload["3_COST_SENSITIVITY_MATRIX"]["profit_to_loss_combos"]),
        "## 4_UNIFIED_COMBO_TABLE\n\n" + _markdown_table(payload["4_UNIFIED_COMBO_TABLE"]),
        "## 5_ANALYTICAL_GUARDRAILS\n\n" + "\n".join(f"- {item}" for item in payload["5_ANALYTICAL_GUARDRAILS"]),
    ]
    return AIAnalysisBundle(payload=payload, markdown="\n\n".join(sections) + "\n", json_text=json_text)
