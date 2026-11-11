"""Recompute Backtest Metrics independently using ONLY pandas.

Does NOT import autotrade_v3.analysis.* to ensure independent verification.
Reads trades.parquet (or trades.csv) and outputs:
- Signal, entry, and leg counts breakdown by symbol
- Gross PnL, Total Fees, Net PnL
- Win Rate at Trade level and Leg level
- Exit reason distribution
"""
from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd


def recompute_metrics(trades_path: str | Path) -> dict:
    p = Path(trades_path)
    if not p.exists():
        raise FileNotFoundError(f"Trades file not found: {p}")

    if p.suffix.lower() == ".parquet":
        df = pd.read_parquet(p)
    else:
        df = pd.read_csv(p)

    total_legs = len(df)
    has_signals = "signal_id" in df.columns
    total_signals = df["signal_id"].nunique() if has_signals else None
    total_entries = df["trade_id"].nunique()

    # Breakdown by symbol
    sym_rows = []
    for sym, group in df.groupby("symbol"):
        sig_cnt = group["signal_id"].nunique() if has_signals else "-"
        entry_cnt = group["trade_id"].nunique()
        leg_cnt = len(group)
        gross = float(group["gross_pnl"].sum())
        fees = float(group["fee_total"].sum())
        net = float(group["net_pnl"].sum())
        sym_rows.append({
            "symbol": sym,
            "signals": sig_cnt,
            "entries": entry_cnt,
            "legs": leg_cnt,
            "gross_pnl": gross,
            "fees": fees,
            "net_pnl": net,
        })

    # Overall financial accounting
    total_gross = float(df["gross_pnl"].sum())
    total_fees = float(df["fee_total"].sum())
    total_net = float(df["net_pnl"].sum())

    # Trade-level Win Rate
    trade_agg = df.groupby("trade_id")["net_pnl"].sum()
    trade_wins = int((trade_agg > 0).sum())
    trade_losses = int((trade_agg < 0).sum())
    trade_win_rate = (trade_wins / len(trade_agg) * 100.0) if len(trade_agg) > 0 else 0.0

    # Leg-level Win Rate
    leg_wins = int((df["net_pnl"] > 0).sum())
    leg_losses = int((df["net_pnl"] < 0).sum())
    leg_win_rate = (leg_wins / total_legs * 100.0) if total_legs > 0 else 0.0

    # Exit reason distribution
    exit_counts = df["exit_reason"].value_counts().to_dict()
    exit_dist = {
        reason: {
            "count": int(count),
            "pct": float(count / total_legs * 100.0),
        }
        for reason, count in exit_counts.items()
    }

    return {
        "trades_path": str(p),
        "total_signals": total_signals,
        "total_entries": total_entries,
        "total_legs": total_legs,
        "by_symbol": sym_rows,
        "total_gross_pnl": total_gross,
        "total_fees": total_fees,
        "total_net_pnl": total_net,
        "trade_wins": trade_wins,
        "trade_losses": trade_losses,
        "trade_win_rate_pct": trade_win_rate,
        "leg_wins": leg_wins,
        "leg_losses": leg_losses,
        "leg_win_rate_pct": leg_win_rate,
        "exit_reason_dist": exit_dist,
    }


def print_metrics_report(metrics: dict) -> None:
    print("=== AutoTrade V3 Independent Metrics Recomputation ===")
    print(f"Source Ledger: {metrics['trades_path']}")
    sig_str = f"{metrics['total_signals']:,}" if metrics['total_signals'] is not None else "N/A"
    print(f"Total Unique Signals: {sig_str}")
    print(f"Total Accepted Trades (Entries): {metrics['total_entries']:,}")
    print(f"Total Executed Legs:            {metrics['total_legs']:,}")

    print("\n--- Breakdown By Symbol ---")
    fmt_sym = "{:<12} {:<10} {:<10} {:<10} {:<15} {:<15} {:<15}"
    print(fmt_sym.format("Symbol", "Signals", "Entries", "Legs", "Gross PnL ($)", "Fees ($)", "Net PnL ($)"))
    print("-" * 90)
    for row in metrics["by_symbol"]:
        sig_val = str(row["signals"]) if row["signals"] != "-" else "-"
        print(fmt_sym.format(
            row["symbol"],
            sig_val,
            f"{row['entries']:,}",
            f"{row['legs']:,}",
            f"{row['gross_pnl']:,.4f}",
            f"{row['fees']:,.4f}",
            f"{row['net_pnl']:,.4f}",
        ))
    print("-" * 90)
    print(fmt_sym.format(
        "TOTAL",
        sig_str,
        f"{metrics['total_entries']:,}",
        f"{metrics['total_legs']:,}",
        f"{metrics['total_gross_pnl']:,.4f}",
        f"{metrics['total_fees']:,.4f}",
        f"{metrics['total_net_pnl']:,.4f}",
    ))

    print("\n--- Win Rate Statistics ---")
    print(f"Trade-level Win Rate: {metrics['trade_win_rate_pct']:.2f}% ({metrics['trade_wins']}/{metrics['total_entries']})")
    print(f"Leg-level Win Rate:   {metrics['leg_win_rate_pct']:.2f}% ({metrics['leg_wins']}/{metrics['total_legs']})")

    print("\n--- Exit Reason Distribution ---")
    fmt_exit = "{:<20} {:<12} {:<10}"
    print(fmt_exit.format("Exit Reason", "Count", "Percentage"))
    print("-" * 45)
    for reason, data in metrics["exit_reason_dist"].items():
        print(fmt_exit.format(reason, f"{data['count']:,}", f"{data['pct']:.2f}%"))
    print("-" * 45)


def main() -> None:
    parser = argparse.ArgumentParser(description="Recompute metrics independently using pandas only")
    parser.add_argument("trades_path", help="Path to trades.parquet or trades.csv")
    args = parser.parse_args()

    m = recompute_metrics(args.trades_path)
    print_metrics_report(m)


if __name__ == "__main__":
    main()
