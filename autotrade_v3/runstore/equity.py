"""Equity curve builders for RunStore (`equity.parquet`)."""
from __future__ import annotations

import pandas as pd


def build_equity_df(
    *,
    mode: str,
    initial_equity: float,
    legs_df: pd.DataFrame,
    snapshots_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build canonical equity.parquet DataFrame.

    - Mode C (``BINGX_PORTFOLIO``): M1 mark-to-market curve with ``equity_kind = 'M1_MTM'``
      and columns ``time_ms, balance, unrealized, equity, used_margin, free_margin, equity_kind``.
    - Mode A / Mode B: closed-trade cumulative net PnL at leg exit time with
      ``equity_kind = 'CLOSED_TRADE'``.
    """
    if mode == "BINGX_PORTFOLIO":
        if snapshots_df is not None and not snapshots_df.empty:
            df = snapshots_df.copy()
            rename_map = {}
            if "timestamp_ms" in df.columns and "time_ms" not in df.columns:
                rename_map["timestamp_ms"] = "time_ms"
            if "unrealized_pnl" in df.columns and "unrealized" not in df.columns:
                rename_map["unrealized_pnl"] = "unrealized"
            if "wallet_balance" in df.columns and "balance" not in df.columns:
                rename_map["wallet_balance"] = "balance"
            if rename_map:
                df = df.rename(columns=rename_map)
            for col in ("time_ms", "balance", "unrealized", "equity", "used_margin", "free_margin"):
                if col not in df.columns:
                    df[col] = 0.0
            df["equity_kind"] = "M1_MTM"
            return df[["time_ms", "balance", "unrealized", "equity", "used_margin", "free_margin", "equity_kind"]]
        return pd.DataFrame(
            {
                "time_ms": pd.Series(dtype="int64"),
                "balance": pd.Series(dtype="float64"),
                "unrealized": pd.Series(dtype="float64"),
                "equity": pd.Series(dtype="float64"),
                "used_margin": pd.Series(dtype="float64"),
                "free_margin": pd.Series(dtype="float64"),
                "equity_kind": pd.Series(dtype="string"),
            }
        )

    if legs_df.empty:
        return pd.DataFrame(
            {
                "time_ms": pd.Series(dtype="int64"),
                "leg_id": pd.Series(dtype="string"),
                "net_pnl": pd.Series(dtype="float64"),
                "balance": pd.Series(dtype="float64"),
                "equity": pd.Series(dtype="float64"),
                "equity_kind": pd.Series(dtype="string"),
            }
        )

    ordered = legs_df.sort_values(["exit_time_ms", "leg_id"], kind="mergesort").reset_index(drop=True)
    cum_net = ordered["net_pnl"].astype(float).cumsum()
    bal = float(initial_equity) + cum_net
    return pd.DataFrame(
        {
            "time_ms": ordered["exit_time_ms"].astype("int64"),
            "leg_id": ordered["leg_id"].astype("string"),
            "net_pnl": ordered["net_pnl"].astype("float64"),
            "balance": bal.astype("float64"),
            "equity": bal.astype("float64"),
            "equity_kind": pd.Series(["CLOSED_TRADE"] * len(ordered), dtype="string"),
        }
    )
