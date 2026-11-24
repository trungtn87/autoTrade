from __future__ import annotations

import os
from typing import Any

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

API = os.environ.get("AUTOTRADE_RESEARCH_API", "http://127.0.0.1:8765")


@st.cache_data(ttl=10, show_spinner=False)
def api_get(path: str, params: tuple[tuple[str, str], ...] = ()) -> Any:
    response = requests.get(f"{API}{path}", params=dict(params), timeout=15)
    response.raise_for_status()
    return response.json()


st.title("Bảng điều khiển V3_LAB_44 · PLUS44")
st.caption("Dữ liệu đã lưu trong database local; dashboard không phụ thuộc tiến trình backtest.")
try:
    all_runs = api_get("/api/research/runs")
except Exception as exc:
    st.error(f"Research API chưa sẵn sàng tại {API}: {exc}")
    st.stop()
if not all_runs:
    st.warning("Chưa có lần chạy backtest trong database.")
    st.stop()

GROUP_LABELS = {
    "PRUNE23_ONLY": "PRUNE23",
    "TV21_ONLY": "TV21",
    "PRUNE23_PLUS_TV21": "PLUS44",
    "PRUNE23_PLUS_TV31": "PLUS54",
    "TV21_STANDALONE": "TV21 · standalone",
    "TV31_STANDALONE": "TV31 · standalone",
    "TV31_ONLY": "TV31",
}
labels = {
    f"{GROUP_LABELS.get(r['study_group'], r['study_group'])} · {r['profile']} · {r['created_at']}": r["run_id"]
    for r in all_runs
}
label_options = list(labels)
default_label = next(
    (label for label in label_options if label.startswith("PLUS44 · BASE ·")),
    label_options[0],
)
run_id = labels[st.selectbox("Lần chạy", label_options, index=label_options.index(default_label))]
overview = api_get("/api/research/overview", (("run_id", run_id),))
metrics = overview["metrics"]
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Chiến lược", overview["strategy_count"])
c2.metric("Net dương", overview["positive_strategies"])
c3.metric("Net PnL", f"${metrics.get('net_pnl', 0):,.2f}")
c4.metric("ROI", f"{metrics.get('roi_pct', 0):.2f}%")
c5.metric("MaxDD", f"${metrics.get('m2m_dd', 0):,.2f}")
c6.metric("Tổng lệnh", f"{metrics.get('executed_trades', 0):,}")
st.caption(f"{overview['start_time']} đến {overview['end_time']} UTC · {overview['profile']}")
if overview["portfolio_valid"]:
    st.info("Kết quả từng mã bên dưới là PnL được phân bổ trong mô phỏng danh mục vốn chung (SHARED_PORTFOLIO_ALLOCATION).")
else:
    st.warning("Đây là kết quả từng mã chạy độc lập (STANDALONE_INDEPENDENT); tổng Net không được xem là PnL danh mục.")

if all(key in metrics for key in ("p1_net", "p2_net", "p3_net")):
    y1, y2, y3 = st.columns(3)
    y1.metric("Năm 1 · 11/2023–10/2024", f"${metrics['p1_net']:,.2f}")
    y2.metric("Năm 2 · 11/2024–10/2025", f"${metrics['p2_net']:,.2f}")
    y3.metric("Năm 3 · 11/2025–10/2026", f"${metrics['p3_net']:,.2f}")

f1, f2, f3, f4 = st.columns(4)
symbol = f1.selectbox("Coin", ["Tất cả", "BTC-USDT", "ETH-USDT"])
timeframe = f2.selectbox("Timeframe", ["Tất cả", "H1", "H4", "H8", "H12", "D1", "15m"])
sign = f3.selectbox("Net", ["ALL", "POSITIVE", "NEGATIVE"])
sort = f4.selectbox("Sắp xếp", ["net_pnl", "roi", "total_trades", "max_drawdown"])
params = [("run_id", run_id), ("sign", sign), ("sort", sort)]
if symbol != "Tất cả": params.append(("symbol", symbol))
if timeframe != "Tất cả": params.append(("timeframe", timeframe))
strategies = pd.DataFrame(api_get("/api/research/strategies", tuple(params)))
if strategies.empty:
    st.info("Không có kết quả phù hợp bộ lọc.")
    st.stop()

table = strategies[["strategy_id", "symbol", "timeframe", "total_trades", "win_rate", "net_pnl", "roi", "max_drawdown"]].copy()
table.columns = ["Mã", "Coin", "TF", "Lệnh", "Win %", "Net PnL", "ROI %", "MaxDD"]
st.dataframe(
    table.style.map(lambda v: "color:#22c55e" if v > 0 else "color:#ef4444", subset=["Net PnL"]),
    hide_index=True, use_container_width=True,
    column_config={"Net PnL": st.column_config.NumberColumn(format="$%.2f"), "ROI %": st.column_config.NumberColumn(format="%.2f%%")},
)

strategy_id = st.selectbox("Chi tiết chiến lược", strategies.strategy_id.tolist())
detail = api_get("/api/research/strategy", (("run_id", run_id), ("strategy_id", strategy_id)))
summary = detail["summary"]
st.subheader(f"{strategy_id} · {summary['symbol']} · {summary['timeframe']}")
st.caption(f"TP {summary.get('tp_pct')} · SL {summary.get('sl_pct')} · {summary['variant']} · {summary['execution_basis']}")
d1, d2, d3, d4, d5 = st.columns(5)
d1.metric("Lệnh", summary["total_trades"]); d2.metric("Win rate", f"{summary['win_rate']:.2f}%")
d3.metric("Net", f"${summary['net_pnl']:.2f}"); d4.metric("Avg hold", f"{summary['avg_hold_hours']:.1f}h")
d5.metric("Max hold", f"{summary['max_hold_hours']:.1f}h")

equity = pd.DataFrame(api_get("/api/research/equity", (("run_id", run_id),)))
if not equity.empty:
    equity["time"] = pd.to_datetime(equity.timestamp_ms, unit="ms", utc=True)
    st.plotly_chart(px.line(equity, x="time", y="equity", title="Equity danh mục"), use_container_width=True)
monthly = pd.DataFrame(detail["monthly"])
if not monthly.empty:
    st.plotly_chart(px.bar(monthly, x="month", y="net_pnl", title="Net PnL theo tháng"), use_container_width=True)
trade_frame = pd.DataFrame(detail["trades"])
if not trade_frame.empty:
    st.subheader("Giao dịch")
    st.dataframe(trade_frame, hide_index=True, use_container_width=True)
