"""MT5-style Run Detail and AI-ready context bundle exporter."""
from __future__ import annotations

import base64
from datetime import date
import html

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from autotrade_v3.core.report_engine import build_ai_analysis_bundle, build_run_report, simulate_cost_sensitivity
from autotrade_v3.dashboard import queries
from autotrade_v3.dashboard.ui_shared import get_store_dir, style_plotly


PREFERRED_RESEARCH_RUN = "R_86b3e8b4c96cd4b6"


def _money(value: float, decimals: int = 2) -> str:
    return f"${value:,.{decimals}f}" if value >= 0 else f"−${abs(value):,.{decimals}f}"


def _download_anchor(label: str, content: str, mime: str, filename: str, testid: str) -> str:
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    return (
        f'<a class="mt5-download" data-testid="{html.escape(testid)}" '
        f'href="data:{mime};base64,{encoded}" download="{html.escape(filename)}">{html.escape(label)}</a>'
    )


def _metric_row(label: str, value: str, tone: str = "") -> str:
    return (
        '<div class="mt5-metric-row">'
        f'<span>{html.escape(label)}</span><strong class="{html.escape(tone)}">{html.escape(value)}</strong>'
        "</div>"
    )


def _metric_card(title: str, rows: list[tuple[str, str, str]], testid: str) -> str:
    body = "".join(_metric_row(*row) for row in rows)
    return f'<article class="mt5-card" data-testid="{html.escape(testid)}"><h3>{html.escape(title)}</h3>{body}</article>'


def _result_cell(label: str, value: str) -> str:
    return f'<div class="mt5-result-cell"><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>'


def _profit_color(value: float) -> str:
    return "background-color:#123c31;color:#8ff0c2" if value > 0 else "background-color:#4a2028;color:#ffb5bd" if value < 0 else ""


def _research_run_label(run: dict) -> str:
    """Render enough real RunStore context to prevent similarly named runs being confused."""
    run_id = str(run.get("run_id", ""))
    strategy_ref = f"{run.get('strategy_id', '')}@{run.get('strategy_version', '')}".strip("@")
    entries = int(run.get("accepted_entries", 0) or 0)
    net_pnl = float(run.get("net_pnl", 0.0) or 0.0)
    return f"{run_id} · {strategy_ref} · {entries:,} entries · {_money(net_pnl)}"


def _page_css() -> None:
    st.markdown(
        """
        <style>
        .st-key-machine_reconciliation_metric{display:none}
        .mt5-topbar{display:flex;align-items:center;gap:12px;min-height:52px}.mt5-title{font-size:1.45rem;font-weight:850;color:#fff;letter-spacing:-.02em}.mt5-alert{display:inline-flex;padding:7px 10px;border-radius:8px;background:#4a2028;border:1px solid #8e3745;color:#ffbec5;font-size:.78rem;font-weight:800}.mt5-validation{color:#b9c7d9;font-size:.72rem;margin-top:5px}
        .mt5-download{display:block;text-align:center;padding:11px 12px;border-radius:9px;background:#173e70;border:1px solid #2b639e;color:#fff!important;text-decoration:none!important;font-size:.78rem;font-weight:800;white-space:nowrap}.mt5-download:hover{background:#20538f;border-color:#50c7ff}
        .mt5-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:10px 0 16px}.mt5-card{background:#101927;border:1px solid #35445b;border-radius:12px;overflow:hidden;box-shadow:0 8px 24px rgba(0,0,0,.14)}.mt5-card h3{font-size:.9rem!important;margin:0!important;padding:10px 12px;border-bottom:1px solid #35445b;background:#152132;color:#fff!important}.mt5-metric-row{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:7px 12px;border-bottom:1px solid rgba(75,92,116,.35);font-variant-numeric:tabular-nums}.mt5-metric-row:last-child{border-bottom:0}.mt5-metric-row span{color:#c3cfdd;font-size:.73rem}.mt5-metric-row strong{color:#f8fafc;font-size:.9rem;text-align:right}.mt5-metric-row .good{color:#42e79a}.mt5-metric-row .bad{color:#ff747f}.mt5-metric-row .warn{color:#ffc464}.mt5-metric-row .info{color:#4edcff}
        .mt5-section-marker{height:0;overflow:hidden}.mt5-panel-title{font-size:.92rem;font-weight:800;color:#fff;margin:0 0 8px}.mt5-results{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border:1px solid #35445b;border-radius:11px;overflow:hidden;background:#101927}.mt5-result-cell{display:flex;justify-content:space-between;gap:12px;padding:9px 11px;border-right:1px solid #35445b;border-bottom:1px solid #35445b;font-variant-numeric:tabular-nums}.mt5-result-cell:nth-child(3n){border-right:0}.mt5-result-cell span{font-size:.72rem;color:#c3cfdd}.mt5-result-cell strong{font-size:.78rem;color:#f8fafc;text-align:right}.mt5-grid-table{width:100%;border-collapse:collapse;background:#101927;border:1px solid #35445b;border-radius:11px;overflow:hidden;font-variant-numeric:tabular-nums}.mt5-grid-table th,.mt5-grid-table td{padding:8px 7px;border:1px solid #35445b;text-align:right;font-size:.71rem}.mt5-grid-table th{background:#152132;color:#dbe5f1}.mt5-grid-table th:first-child,.mt5-grid-table td:first-child{text-align:left}.mt5-grid-table td.pos{background:#123c31;color:#8ff0c2}.mt5-grid-table td.neg{background:#4a2028;color:#ffb5bd}
        [data-testid="stDataFrame"]{font-variant-numeric:tabular-nums}.st-key-run_detail_symbol_filter,.st-key-run_detail_leg_filter{margin-top:-4px}
        @media(max-width:1100px){.mt5-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.mt5-results{grid-template-columns:repeat(2,minmax(0,1fr))}.mt5-result-cell:nth-child(3n){border-right:1px solid #35445b}.mt5-result-cell:nth-child(2n){border-right:0}}
        @media(max-width:700px){.mt5-grid{grid-template-columns:1fr}.mt5-results{grid-template-columns:1fr}.mt5-result-cell{border-right:0!important}.mt5-title{font-size:1.15rem}}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _cost_grid_html(sensitivity: dict) -> str:
    frame = sensitivity["portfolio"]
    levels = sorted(frame["slippage_pct"].unique().tolist())
    rows = ["<table class='mt5-grid-table' data-testid='cost-sensitivity-grid'><thead><tr><th>Fee mode</th>"]
    rows.extend(f"<th>{level:.3f}%</th>" for level in levels)
    rows.append("</tr></thead><tbody>")
    for mode in ("IDEAL", "REALISTIC_TAKER_FUNDING"):
        rows.append(f"<tr><td>{html.escape(mode.replace('_', ' '))}</td>")
        for level in levels:
            selected = frame[(frame["fee_mode"] == mode) & (frame["slippage_pct"] == level)]
            value = float(selected["net_pnl"].iloc[0]) if not selected.empty else 0.0
            css_class = "pos" if value > 0 else "neg" if value < 0 else ""
            rows.append(f"<td class='{css_class}'>{html.escape(_money(value))}</td>")
        rows.append("</tr>")
    rows.append("</tbody></table>")
    return "".join(rows)


def _balance_equity_chart(equity: pd.DataFrame, split_date: date) -> go.Figure:
    frame = equity.sort_values("timestamp_dt").copy()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.74, 0.26], vertical_spacing=0.04)
    fig.add_trace(go.Scatter(x=frame["timestamp_dt"], y=frame["equity"], name="Equity", line=dict(color="#50c7ff", width=2.2), fill="tozeroy", fillcolor="rgba(80,199,255,.08)"), row=1, col=1)
    if "balance" in frame.columns and frame["balance"].notna().any():
        fig.add_trace(go.Scatter(x=frame["timestamp_dt"], y=frame["balance"], name="Balance", line=dict(color="#36e394", width=1.3)), row=1, col=1)
    drawdown = -frame["drawdown_pct"].astype(float)
    fig.add_trace(go.Scatter(x=frame["timestamp_dt"], y=drawdown, name="Drawdown %", line=dict(color="#ff6673", width=1.2), fill="tozeroy", fillcolor="rgba(255,102,115,.22)"), row=2, col=1)
    split = pd.Timestamp(split_date, tz="UTC")
    fig.add_vline(x=split.timestamp() * 1000, line_dash="dash", line_color="#d6dee8", line_width=1)
    fig.add_vrect(x0=split, x1=frame["timestamp_dt"].max(), fillcolor="#7c8da5", opacity=.08, line_width=0)
    fig.add_annotation(x=split, y=1.08, yref="paper", text="OUT-OF-SAMPLE", showarrow=False, font=dict(color="#ffc464", size=11))
    fig.update_yaxes(title_text="Balance / Equity ($)", row=1, col=1)
    fig.update_yaxes(title_text="DD %", row=2, col=1)
    fig.update_layout(hovermode="x unified")
    return style_plotly(fig, 500)


def render() -> None:
    _page_css()
    store_dir = get_store_dir()
    runs = queries.list_store_runs(store_dir=store_dir)
    if not runs:
        st.info("RunStore đang trống.")
        return

    runs_by_id = {str(run["run_id"]): run for run in runs}
    run_ids = list(runs_by_id)
    default_run_id = PREFERRED_RESEARCH_RUN if PREFERRED_RESEARCH_RUN in runs_by_id else run_ids[0]
    selected_run = st.sidebar.selectbox(
        "Research run",
        run_ids,
        index=run_ids.index(default_run_id),
        format_func=lambda run_id: _research_run_label(runs_by_id[run_id]),
        key="run-detail-select",
    )
    try:
        summary = queries.get_run_summary(selected_run, store_dir=store_dir)
        legs = queries.get_run_legs_ledger(selected_run, store_dir=store_dir)
        decisions = queries.get_run_decisions_drilldown(selected_run, store_dir=store_dir)
        equity = queries.get_run_equity_curve(selected_run, store_dir=store_dir)
        costs = queries.get_run_cost_breakdown(selected_run, store_dir=store_dir)
        strategy = queries.get_strategy_detail("20FINAL@1")["combos_df"]
        report = build_run_report(summary, legs, decisions, equity, strategy, costs)
        sensitivity = simulate_cost_sensitivity(report)
        bundle = build_ai_analysis_bundle(report, sensitivity)
    except (queries.MissingArtifactError, FileNotFoundError, ValueError, KeyError) as exc:
        st.error(f"Không thể dựng Run Detail: {exc}")
        return

    metadata, portfolio = report["metadata"], report["portfolio"]
    filename_base = f"{metadata['strategy_ref'].replace('@', '_')}_{selected_run}_ai_bundle"
    title_col, md_col, json_col = st.columns([5.3, 1.35, 1.35])
    with title_col:
        alert = ""
        if metadata["fee_mode"].upper() == "IDEAL":
            alert = '<span class="mt5-alert">⚠ FEE MODE: IDEAL · 0 Slippage / 0 Funding</span>'
        st.markdown(
            f'<div class="mt5-topbar" data-testid="run-detail-topbar"><span class="mt5-title">AutoTrade V3 Research Lab · Run Detail · {html.escape(metadata["strategy_ref"])}</span>{alert}</div>'
            f'<div class="mt5-validation">Run {html.escape(selected_run)} · {html.escape(metadata["execution_mode"])} · Validation {html.escape(metadata["validation_status"])}</div>',
            unsafe_allow_html=True,
        )
    with md_col:
        st.markdown(_download_anchor("📋 Export AI Bundle (.md)", bundle.markdown, "text/markdown;charset=utf-8", f"{filename_base}.md", "export-ai-bundle-btn"), unsafe_allow_html=True)
    with json_col:
        st.markdown(_download_anchor("📦 Export AI Bundle (.json)", bundle.json_text, "application/json;charset=utf-8", f"{filename_base}.json", "export-ai-bundle-btn"), unsafe_allow_html=True)

    max_dd = f"{_money(portfolio['max_drawdown_dollars'])} / {portfolio['max_drawdown_pct']:.2f}%"
    break_even_tone = "bad" if portfolio["break_even_slippage_per_side_pct"] < 0.05 else "good"
    metric_html = '<section class="mt5-grid" data-testid="run-detail-metric-grid">'
    metric_html += _metric_card("Account", [
        ("Initial Capital", _money(portfolio["initial_capital"]), ""),
        ("Net PnL", _money(portfolio["net_pnl"]), "good" if portfolio["net_pnl"] >= 0 else "bad"),
        ("ROI", f"{portfolio['roi_pct']:+.2f}%", "good" if portfolio["roi_pct"] >= 0 else "bad"),
        ("Ending Balance", _money(portfolio["ending_balance"]), "info"),
    ], "account-metrics")
    metric_html += _metric_card("Cost Reality", [
        ("Total Fees", _money(portfolio["fees"]), ""),
        ("Fee Drag", f"{portfolio['fee_drag_pct']:.2f}%", "warn"),
        ("Net PnL / Entry", _money(portfolio["net_pnl_per_entry"]), ""),
        ("Break-Even Slippage", f"{portfolio['break_even_slippage_per_side_pct']:.3f}%/side", break_even_tone),
    ], "cost-reality-metrics")
    metric_html += _metric_card("Risk", [
        ("Max Drawdown", max_dd, "warn"),
        ("Recovery Factor", f"{portfolio['recovery_factor']:.2f}", ""),
        ("Sharpe Ratio", f"{portfolio['sharpe_ratio']:.2f}", ""),
        ("Weighted PF", f"{portfolio['weighted_pf']:.2f}", "info"),
    ], "risk-metrics")
    metric_html += _metric_card("Execution", [
        ("Filtered Signals", f"{portfolio['signals']:,}", ""),
        ("Executed Entries", f"{portfolio['entries']:,}", ""),
        ("Pos Fill Rate", f"{portfolio['pos_fill_rate_pct']:.1f}%", "info"),
        ("Leg Win Rate", f"{portfolio['leg_win_rate_pct']:.1f}%", ""),
    ], "execution-metrics")
    metric_html += "</section>"
    st.markdown(metric_html, unsafe_allow_html=True)

    with st.container(key="machine_reconciliation_metric"):
        st.metric("Net PnL (Simulation)", f"${summary['net_pnl']:,.4f}")

    st.markdown('<div class="mt5-section-marker" data-testid="balance-equity-chart"></div>', unsafe_allow_html=True)
    chart_head, split_col = st.columns([5, 1.2])
    chart_head.markdown('<div class="mt5-panel-title">Balance / Equity & Drawdown</div>', unsafe_allow_html=True)
    min_date = equity["timestamp_dt"].min().date()
    max_date = equity["timestamp_dt"].max().date()
    default_split = min(max(date(2026, 1, 1), min_date), max_date)
    split_date = split_col.date_input("IS / OOS split", value=default_split, min_value=min_date, max_value=max_date, key="run-detail-split-date")
    st.plotly_chart(_balance_equity_chart(equity, split_date), use_container_width=True, key="run-detail-balance-equity")

    symbols = {row["group"]: row for row in report["breakdowns"]["Symbol"]}
    leg_groups = {row["group"]: row for row in report["breakdowns"]["Leg Structure"]}
    buy = report["side_stats"].get("BUY", report["side_stats"].get("LONG", {"trades": 0, "win_rate_pct": 0.0}))
    sell = report["side_stats"].get("SELL", report["side_stats"].get("SHORT", {"trades": 0, "win_rate_pct": 0.0}))
    result_cells = [
        ("Total Net Profit", _money(portfolio["net_pnl"])),
        ("Gross Profit", _money(portfolio["gross_profit"])),
        ("Gross Loss", _money(portfolio["gross_loss"])),
        ("Weighted Profit Factor", f"{portfolio['weighted_pf']:.2f}"),
        ("Expected Payoff", _money(portfolio["net_pnl_per_entry"])),
        ("Sharpe Ratio", f"{portfolio['sharpe_ratio']:.2f}"),
        ("Long Trades (won %)", f"{buy['trades']:,} ({buy['win_rate_pct']:.2f}%)"),
        ("Short Trades (won %)", f"{sell['trades']:,} ({sell['win_rate_pct']:.2f}%)"),
        ("Total Trades", f"{portfolio['entries']:,}"),
        ("BTC Net / Own", f"{_money(symbols.get('BTC-USDT', {}).get('net_pnl', 0.0))} / {_money(symbols.get('BTC-USDT', {}).get('pnl_own_entry', 0.0))}"),
        ("ETH Net / Own", f"{_money(symbols.get('ETH-USDT', {}).get('net_pnl', 0.0))} / {_money(symbols.get('ETH-USDT', {}).get('pnl_own_entry', 0.0))}"),
        ("1-Leg / 2-Leg Own PnL", f"{_money(leg_groups.get('1-Leg', {}).get('pnl_own_entry', 0.0))} / {_money(leg_groups.get('2-Leg', {}).get('pnl_own_entry', 0.0))}"),
    ]
    results_col, cost_col = st.columns([7, 3])
    with results_col:
        st.markdown('<div class="mt5-panel-title">Results</div><div class="mt5-results" data-testid="mt5-results-table">' + "".join(_result_cell(*cell) for cell in result_cells) + "</div>", unsafe_allow_html=True)
    with cost_col:
        st.markdown('<div class="mt5-panel-title">Cost Sensitivity Grid</div>' + _cost_grid_html(sensitivity), unsafe_allow_html=True)

    st.markdown('<div class="mt5-section-marker" data-testid="unified-combo-master-table"></div>', unsafe_allow_html=True)
    st.subheader("Unified Combo Master Table")
    filter_a, filter_b, filler = st.columns([1.2, 1.2, 4])
    symbol_filter = filter_a.selectbox("Symbol", ["All", "BTC-USDT", "ETH-USDT"], key="run_detail_symbol_filter")
    leg_filter = filter_b.selectbox("Leg Structure", ["All", "1-Leg", "2-Leg"], key="run_detail_leg_filter")
    table = report["combo_table"].copy()
    if symbol_filter != "All":
        table = table[table["symbol"] == symbol_filter]
    if leg_filter != "All":
        table = table[table["leg_structure"] == leg_filter]
    display = pd.DataFrame({
        "Combo Key": table["combo_key"],
        "TF": table["timeframe"],
        "Legs": table["leg_structure"] + " " + table["leg_weights"],
        "Indicator": table["entry_variant"],
        "Filter": table["filters"],
        "TP/SL (R:R)": table.apply(lambda row: f"{row['tp_pct']:.2%}/{row['sl_pct']:.2%} ({row['rr_ratio']:.2f})", axis=1),
        "Entries/Signals": table.apply(lambda row: f"{int(row['entries']):,}/{int(row['signals']):,}", axis=1),
        "Fees ($)": table["fees"],
        "Own Entry PnL ($)": table["pnl_own_entry"],
        "Portfolio Net PnL ($)": table["net_pnl"],
        "Delta ($)": table["attribution_delta"],
        "Trade PF": table["weighted_pf"],
    })
    styled = display.style.format({
        "Fees ($)": "${:,.2f}", "Own Entry PnL ($)": "${:+,.2f}", "Portfolio Net PnL ($)": "${:+,.2f}",
        "Delta ($)": "${:+,.2f}", "Trade PF": "{:.2f}",
    }).map(_profit_color, subset=["Own Entry PnL ($)", "Portfolio Net PnL ($)", "Delta ($)"])
    # Pixel widths keep the complete attribution path visible on a 1440 px desktop.
    # The raw numeric values remain available to sorting despite the compact display.
    combo_column_config = {
        "Combo Key": st.column_config.TextColumn("Combo Key", width=112),
        "TF": st.column_config.TextColumn("TF", width=48),
        "Legs": st.column_config.TextColumn("Legs", width=105),
        "Indicator": st.column_config.TextColumn("Indicator", width=112),
        "Filter": st.column_config.TextColumn("Filter", width=112),
        "TP/SL (R:R)": st.column_config.TextColumn("TP/SL (R:R)", width=125),
        "Entries/Signals": st.column_config.TextColumn("Entries/Signals", width=105),
        "Fees ($)": st.column_config.NumberColumn("Fees", format="$%.2f", width=76),
        "Own Entry PnL ($)": st.column_config.NumberColumn("Own Entry PnL", format="$%.2f", width=112),
        "Portfolio Net PnL ($)": st.column_config.NumberColumn("Portfolio Net PnL", format="$%.2f", width=128),
        "Delta ($)": st.column_config.NumberColumn("Delta", format="$%.2f", width=80),
        "Trade PF": st.column_config.NumberColumn("PF", format="%.2f", width=58),
    }
    st.dataframe(
        styled,
        use_container_width=True,
        column_config=combo_column_config,
        hide_index=True,
        height=620,
        key="run-detail-combo-table",
    )


if __name__ == "__main__":
    render()
