"""Report generation exporting CSV, Parquet, JSON, and standalone HTML dashboard."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any
import pandas as pd

from autotrade_v3.analysis.metrics import BacktestMetrics
from autotrade_v3.engine.execution_simulator import TradeRecord


def export_trades_csv(trades: list[TradeRecord], out_path: Path) -> None:
    """Export trade ledger to CSV."""
    if not trades:
        out_path.write_text("trade_id\n", encoding="utf-8")
        return

    fieldnames = list(trades[0].to_dict().keys())
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            writer.writerow(t.to_dict())


def export_trades_parquet(trades: list[TradeRecord], out_path: Path) -> None:
    """Export trade ledger to Parquet for DuckDB querying."""
    rows = [t.to_dict() for t in trades]
    df = pd.DataFrame(rows)
    df.to_parquet(out_path, index=False)


def export_summary_json(
    metrics: BacktestMetrics,
    regimes: dict[str, Any],
    metadata: dict[str, Any],
    out_path: Path,
) -> None:
    """Export summary metrics, regime splits, and run metadata to JSON."""
    data = {
        "metadata": metadata,
        "overall_metrics": metrics.to_dict(),
        "regimes": regimes,
    }
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def generate_html_report(
    metrics: BacktestMetrics,
    regimes: dict[str, Any],
    metadata: dict[str, Any],
    out_path: Path,
) -> None:
    """Generate self-contained HTML dashboard report."""
    # Build combo rows
    combo_rows = []
    for cname, c in sorted(metrics.combo_breakdown.items()):
        pnl_color = "#10b981" if c["net_pnl"] >= 0 else "#ef4444"
        row = f"""
        <tr>
            <td style="font-weight:600;">{cname}</td>
            <td>{c['symbol']}</td>
            <td>{c['timeframe']}</td>
            <td>{c['total_trades']}</td>
            <td>{c['win_rate_pct']:.1f}%</td>
            <td style="color:{pnl_color}; font-weight:600;">${c['net_pnl']:,.2f}</td>
            <td>{c['profit_factor']:.2f}</td>
            <td>{c['intrabar_ambiguous']}</td>
        </tr>
        """
        combo_rows.append(row)

    # Build monthly rows
    monthly_rows = []
    for m, pnl in sorted(metrics.monthly_pnl.items()):
        pnl_color = "#10b981" if pnl >= 0 else "#ef4444"
        monthly_rows.append(f"""
        <tr>
            <td>{m}</td>
            <td style="color:{pnl_color}; font-weight:600;">${pnl:,.2f}</td>
        </tr>
        """)

    # Build equity curve points for simple SVG line chart
    eq_points = metrics.equity_curve
    svg_polyline = ""
    if len(eq_points) > 1:
        equities = [p["equity"] for p in eq_points]
        min_eq, max_eq = min(equities), max(equities)
        eq_range = max(1e-5, max_eq - min_eq)
        w, h = 800, 200
        pts = []
        for i, eq in enumerate(equities):
            x = (i / (len(equities) - 1)) * w
            y = h - ((eq - min_eq) / eq_range) * (h - 20) - 10
            pts.append(f"{x:.1f},{y:.1f}")
        svg_polyline = " ".join(pts)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AutoTrade V3 Local Backtest Report</title>
<style>
  :root {{
    --bg: #0f172a;
    --card: #1e293b;
    --text: #f8fafc;
    --muted: #94a3b8;
    --accent: #38bdf8;
    --success: #10b981;
    --danger: #ef4444;
    --border: #334155;
  }}
  body {{
    margin: 0;
    padding: 24px;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  header {{ margin-bottom: 24px; border-bottom: 1px solid var(--border); padding-bottom: 16px; }}
  h1 {{ margin: 0 0 8px 0; font-size: 24px; }}
  .badge {{ background: #0369a1; color: #e0f2fe; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; display: inline-block; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }}
  .card-title {{ font-size: 13px; color: var(--muted); text-transform: uppercase; margin-bottom: 8px; }}
  .card-value {{ font-size: 24px; font-weight: 700; }}
  .card-sub {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14px; }}
  th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; }}
  .chart-box {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 24px; }}
  .regime-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 24px; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <div style="display: flex; justify-content: space-between; align-items: center;">
      <div>
        <h1>AutoTrade V3 — Local Backtest Engine Report</h1>
        <div style="color: var(--muted); font-size: 14px;">
          Execution Mode: <strong>{metadata.get('mode', 'VIRTUAL_ISOLATED')}</strong> &bull;
          Git Commit: <code>{metadata.get('git_commit', 'N/A')}</code> &bull;
          Generated: {metadata.get('generated_at', '')}
        </div>
      </div>
      <div>
        <span class="badge">SIMULATION ONLY</span>
      </div>
    </div>
  </header>

  <!-- Metric Cards -->
  <div class="grid">
    <div class="card">
      <div class="card-title">Net Realized PnL</div>
      <div class="card-value" style="color: {'var(--success)' if metrics.net_pnl >= 0 else 'var(--danger)'};">
        ${metrics.net_pnl:,.2f}
      </div>
      <div class="card-sub">ROI: {metrics.roi_pct:+.2f}% on initial capital</div>
    </div>
    <div class="card">
      <div class="card-title">Win Rate</div>
      <div class="card-value">{metrics.win_rate_pct:.1f}%</div>
      <div class="card-sub">{metrics.winning_trades} W / {metrics.losing_trades} L / {metrics.breakeven_trades} E</div>
    </div>
    <div class="card">
      <div class="card-title">Profit Factor</div>
      <div class="card-value">{metrics.profit_factor:.2f}</div>
      <div class="card-sub">Avg Trade: ${metrics.avg_trade_pnl:,.2f}</div>
    </div>
    <div class="card">
      <div class="card-title">Max Drawdown</div>
      <div class="card-value" style="color: var(--danger);">${metrics.max_drawdown_dollars:,.2f}</div>
      <div class="card-sub">{metrics.max_drawdown_pct:.1f}% peak-to-trough</div>
    </div>
    <div class="card">
      <div class="card-title">Total Fees</div>
      <div class="card-value">${metrics.total_fees:,.2f}</div>
      <div class="card-sub">Total Trades: {metrics.total_trades}</div>
    </div>
    <div class="card">
      <div class="card-title">Intrabar Ambiguity</div>
      <div class="card-value">{metrics.intrabar_ambiguous_trades}</div>
      <div class="card-sub">{metrics.intrabar_ambiguous_pct:.1f}% resolved conservatively (SL first)</div>
    </div>
  </div>

  <!-- Equity Chart -->
  <div class="chart-box">
    <div class="card-title" style="margin-bottom: 12px;">Cumulative Simulated Equity Curve</div>
    <svg viewBox="0 0 800 200" style="width: 100%; height: 200px; overflow: visible;">
      <polyline fill="none" stroke="#38bdf8" stroke-width="2.5" points="{svg_polyline}" />
    </svg>
  </div>

  <!-- Multi-window Regimes -->
  <h2 style="font-size: 18px; margin: 24px 0 12px 0;">Multi-Window Evaluation Regimes</h2>
  <div class="regime-grid">
    <div class="card">
      <div class="card-title">1. Development (24M)</div>
      <div style="font-size: 12px; color: var(--muted); margin-bottom: 8px;">2023-11-01 &rarr; 2025-10-31</div>
      <div style="font-size: 20px; font-weight: 700; color: {'var(--success)' if regimes.get('DEVELOPMENT', {}).get('net_pnl', 0) >= 0 else 'var(--danger)'};">
        ${regimes.get('DEVELOPMENT', {}).get('net_pnl', 0):,.2f}
      </div>
      <div class="card-sub">
        Trades: {regimes.get('DEVELOPMENT', {}).get('total_trades', 0)} &bull;
        Win Rate: {regimes.get('DEVELOPMENT', {}).get('win_rate_pct', 0):.1f}% &bull;
        PF: {regimes.get('DEVELOPMENT', {}).get('profit_factor', 0):.2f}
      </div>
    </div>
    <div class="card">
      <div class="card-title">2. Validation (6M)</div>
      <div style="font-size: 12px; color: var(--muted); margin-bottom: 8px;">2025-11-01 &rarr; 2026-04-30</div>
      <div style="font-size: 20px; font-weight: 700; color: {'var(--success)' if regimes.get('VALIDATION', {}).get('net_pnl', 0) >= 0 else 'var(--danger)'};">
        ${regimes.get('VALIDATION', {}).get('net_pnl', 0):,.2f}
      </div>
      <div class="card-sub">
        Trades: {regimes.get('VALIDATION', {}).get('total_trades', 0)} &bull;
        Win Rate: {regimes.get('VALIDATION', {}).get('win_rate_pct', 0):.1f}% &bull;
        PF: {regimes.get('VALIDATION', {}).get('profit_factor', 0):.2f}
      </div>
    </div>
    <div class="card">
      <div class="card-title">3. Holdout (6M)</div>
      <div style="font-size: 12px; color: var(--muted); margin-bottom: 8px;">2026-05-01 &rarr; 2026-10-31</div>
      <div style="font-size: 20px; font-weight: 700; color: {'var(--success)' if regimes.get('HOLDOUT', {}).get('net_pnl', 0) >= 0 else 'var(--danger)'};">
        ${regimes.get('HOLDOUT', {}).get('net_pnl', 0):,.2f}
      </div>
      <div class="card-sub">
        Trades: {regimes.get('HOLDOUT', {}).get('total_trades', 0)} &bull;
        Win Rate: {regimes.get('HOLDOUT', {}).get('win_rate_pct', 0):.1f}% &bull;
        PF: {regimes.get('HOLDOUT', {}).get('profit_factor', 0):.2f}
      </div>
    </div>
  </div>

  <!-- Combo Breakdown Table -->
  <div class="card" style="margin-bottom: 24px;">
    <div class="card-title">20FINAL Combo Performance Breakdown</div>
    <table>
      <thead>
        <tr>
          <th>Combo</th>
          <th>Symbol</th>
          <th>Timeframe</th>
          <th>Trades</th>
          <th>Win Rate</th>
          <th>Net PnL</th>
          <th>Profit Factor</th>
          <th>Intrabar Ambiguous</th>
        </tr>
      </thead>
      <tbody>
        {''.join(combo_rows)}
      </tbody>
    </table>
  </div>

  <!-- Monthly Breakdown Table -->
  <div class="card">
    <div class="card-title">Monthly Net PnL</div>
    <table>
      <thead>
        <tr>
          <th>Month (UTC)</th>
          <th>Realized Net PnL</th>
        </tr>
      </thead>
      <tbody>
        {''.join(monthly_rows)}
      </tbody>
    </table>
  </div>
</div>
</body>
</html>
"""
    out_path.write_text(html_content, encoding="utf-8")
