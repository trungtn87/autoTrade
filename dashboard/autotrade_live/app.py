"""Read-only Live Trading dashboard: BingX execution results by Autotrade Combo."""
from __future__ import annotations
import hmac
import io
import os
from datetime import datetime, timezone
import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import select
from .db import Fill, ForceOrder, Income, Order, PositionHistory, SignalLink, SyncRun, get_engine, get_sessionmaker, init_db
from .analytics import build_trades, summarize

st.set_page_config(page_title='Autotrade · Live Analytics', page_icon='📈', layout='wide')


def secure_page():
    password = os.getenv('DASHBOARD_PASSWORD', '')
    if not password:
        st.error('DASHBOARD_PASSWORD chưa được cấu hình. Dashboard từ chối mở để bảo vệ dữ liệu giao dịch.')
        st.stop()
    if st.session_state.get('authenticated'):
        return
    st.title('🔐 Autotrade Live Analytics')
    with st.form('login'):
        candidate = st.text_input('Mật khẩu dashboard', type='password')
        if st.form_submit_button('Đăng nhập'):
            if hmac.compare_digest(candidate.encode(), password.encode()):
                st.session_state.authenticated = True
                st.rerun()
            st.error('Sai mật khẩu')
    st.stop()


secure_page()

st.markdown('''<style>
.block-container {padding-top:1.7rem; max-width:1500px}
[data-testid="stMetric"] {border:1px solid rgba(127,127,127,.2); border-radius:14px; padding:16px; background:rgba(100,116,139,.04)}
h1 {letter-spacing:-.035em}
</style>''', unsafe_allow_html=True)

engine = get_engine()
init_db(engine)
Session = get_sessionmaker(engine)

with Session() as session:
    orders = session.scalars(select(Order)).all()
    fills = session.scalars(select(Fill).order_by(Fill.timestamp_ms)).all()
    incomes = session.scalars(select(Income)).all()
    forces = session.scalars(select(ForceOrder)).all()
    links = session.scalars(select(SignalLink)).all()
    positions = session.scalars(select(PositionHistory)).all()
    sync_runs = session.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(15)).all()

completed, pending, unresolved = build_trades(fills, orders, links, forces)
trades = pd.DataFrame(completed)

st.caption('AUTOTRADE / BINGX USDT PERPETUAL · REAL EXECUTIONS')
st.title('Live Trading Analytics')
st.caption('Dữ liệu thực tế BingX • Không đặt lệnh • Backtest tách riêng')

last = sync_runs[0] if sync_runs else None
if last:
    st.caption(f'Lần đồng bộ gần nhất: {last.started_at} · {last.status.upper()} · {last.details}')
else:
    st.info('Chưa có dữ liệu. Cấu hình API key chỉ đọc, chạy sync và gắn mã Combo với orderId.')
if os.environ.get('DATABASE_URL', '').startswith('sqlite') or not os.environ.get('DATABASE_URL'):
    st.warning('Đang lưu SQLite. Nếu triển khai trên Render, cần DATABASE_URL PostgreSQL để không mất dữ liệu sau restart/deploy.')

with st.sidebar:
    st.header('Bộ lọc')
    symbols = sorted(set(x.symbol for x in orders + fills if x.symbol))
    symbol_choice = st.multiselect('Cặp giao dịch', symbols, default=symbols)
    combos = sorted(set(x.combo for x in links))
    combo_choice = st.multiselect('Combo', combos, default=combos)
    st.caption('Chỉ tính Win Rate theo lệnh ĐÃ ĐÓNG và ĐÃ GẮN COMBO.')
    if st.button('Tải lại dữ liệu'):
        st.rerun()

visible = trades.copy()
if not visible.empty:
    visible = visible[visible['symbol'].isin(symbol_choice)]
    min_ms = int(visible['exit_ms'].min()) if not visible.empty else None
    max_ms = int(visible['exit_ms'].max()) if not visible.empty else None
    if min_ms:
        lower = datetime.fromtimestamp(min_ms / 1000, tz=timezone.utc).date()
        upper = datetime.fromtimestamp(max_ms / 1000, tz=timezone.utc).date()
        with st.sidebar:
            date_range = st.date_input('Khoảng thời gian đóng lệnh (UTC)', value=(lower, upper), min_value=lower, max_value=upper)
        if isinstance(date_range, tuple) and len(date_range) == 2:
            lo, hi = date_range
            dates = pd.to_datetime(visible['exit_ms'], unit='ms', utc=True).dt.date
            visible = visible[(dates >= lo) & (dates <= hi)]

assigned = visible[visible['combo'].isin(combo_choice)] if not visible.empty else visible
unassigned = visible[visible['combo'].isna()] if not visible.empty else visible

k1, k2, k3, k4, k5 = st.columns(5)
net_total = sum(f.realized_pnl + f.fee for f in fills if f.symbol in symbol_choice)
combo_net_total = float(assigned.pnl.sum()) if len(assigned) else 0
win_rate = ((assigned.pnl > 0).mean() * 100) if len(assigned) else 0
k1.metric('PnL Combo đã chọn', f'{combo_net_total:+,.3f} USDT')
k2.metric('Win rate theo Combo', f'{win_rate:.1f}%')
k3.metric('Vị thế đóng, có Combo', f'{len(assigned):,}')
k4.metric('Lệnh chưa gắn / hỗn hợp', f'{len(unassigned):,}')
k5.metric('Thanh lý / ADL đã lưu', f'{sum(f.symbol in symbol_choice for f in forces):,}')
st.caption(f'Đối soát: Tổng realized PnL + phí trên tất cả fills đã lưu của các cặp được chọn: {net_total:+,.3f} USDT (không bao gồm funding). Số thanh lý hiển thị cho toàn bộ thời gian đã lưu; bộ lọc ngày và Combo áp dụng cho kết quả vị thế.')

overview, bycombo, history, audit, mapping = st.tabs(['📈 Tổng quan', '🧩 Theo Combo', '📋 Nhật ký', '🛡️ Đối soát', '🔗 Gắn Combo'])

with overview:
    c1, c2 = st.columns([1.7, 1])
    with c1:
        st.subheader('Lợi nhuận lũy kế · lệnh có Combo')
        if len(assigned):
            graph = assigned.sort_values('exit_ms').copy()
            graph['time'] = pd.to_datetime(graph['exit_ms'], unit='ms', utc=True)
            graph['equity'] = graph['pnl'].cumsum()
            fig = px.line(graph, x='time', y='equity', markers=True, labels={'equity':'PnL lũy kế (USDT)', 'time':'UTC'})
            fig.update_layout(margin=dict(l=8,r=8,t=8,b=8), height=350)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info('Đang thiếu dữ liệu vị thế đã đóng có mã Combo. Gắn entry orderId trong tab “Gắn Combo”.')
    with c2:
        st.subheader('Kết quả theo Combo')
        res = pd.DataFrame(summarize(assigned.to_dict('records') if len(assigned) else []))
        if len(res):
            st.dataframe(res[['Combo','Số lệnh','Win rate %','PnL ròng USDT']], hide_index=True, use_container_width=True)
        else:
            st.caption('Chưa có giao dịch được gắn mã.')
        st.caption(f'Lệnh đang mở: {len(pending)} · Fill cần kiểm tra: {len(unresolved)}')

with bycombo:
    st.subheader('Xếp hạng Combo theo hiệu quả giao dịch thật')
    summary = pd.DataFrame(summarize(assigned.to_dict('records') if len(assigned) else []))
    if len(summary):
        st.dataframe(summary.sort_values('PnL ròng USDT', ascending=False), hide_index=True, use_container_width=True)
        fig = px.bar(summary.sort_values('PnL ròng USDT'), x='PnL ròng USDT', y='Combo', orientation='h', color='PnL ròng USDT', color_continuous_scale='RdYlGn')
        fig.update_layout(coloraxis_showscale=False, height=max(300, 55 * len(summary)))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info('Chưa có dữ liệu Combo đủ điều kiện để tổng hợp.')

with history:
    st.subheader('Nhật ký vị thế đóng')
    if len(visible):
        table = visible.copy()
        for key in ['entry_ms','exit_ms']:
            table[key] = pd.to_datetime(table[key], unit='ms', utc=True).dt.strftime('%Y-%m-%d %H:%M UTC')
        st.dataframe(table[['entry_ms','exit_ms','symbol','side','label','pnl','gross','fees','liquidated','exit_types','entry_order_ids','fills']],
                     hide_index=True, use_container_width=True)
        st.download_button('Xuất CSV lệnh đóng', table.to_csv(index=False).encode('utf-8-sig'), 'autotrade_live_positions.csv', mime='text/csv')
    else:
        st.info('Chưa có lịch sử vị thế đóng trong bộ lọc.')
    if pending:
        st.subheader('Vị thế chưa xác định đóng hết')
        st.dataframe(pd.DataFrame(pending).drop(columns=['entries'], errors='ignore'), hide_index=True, use_container_width=True)

with audit:
    st.subheader('Kiểm tra chất lượng dữ liệu')
    a, b, c, d = st.columns(4)
    a.metric('Orders', len(orders))
    b.metric('Trade fills', len(fills))
    c.metric('Income records', len(incomes))
    d.metric('Position snapshots', len(positions))
    if unresolved:
        st.error(f'{len(unresolved)} fill chưa thể ghép vị thế an toàn. Không tự gán Combo.')
        st.dataframe(pd.DataFrame(unresolved), use_container_width=True, hide_index=True)
    if forces:
        st.markdown('**Thanh lý / ADL theo BingX**')
        st.dataframe(pd.DataFrame([{'symbol':x.symbol, 'reason':x.reason,'orderId':x.order_id,
                                    'timeUTC':datetime.fromtimestamp(x.timestamp_ms/1000, tz=timezone.utc).isoformat() if x.timestamp_ms else ''}
                                   for x in forces]), use_container_width=True, hide_index=True)
    if incomes:
        st.markdown('**Dòng tiền BingX (chưa phân bổ vào Combo)**')
        st.dataframe(pd.DataFrame([{'symbol':x.symbol, 'type':x.income_type, 'amount':x.amount,
                                    'asset':x.asset,'time_ms':x.timestamp_ms} for x in incomes]).tail(100),
                     hide_index=True, use_container_width=True)
    st.markdown('**Đợt đồng bộ**')
    if sync_runs:
        st.dataframe(pd.DataFrame([{'UTC':x.started_at, 'status':x.status, 'details':x.details} for x in sync_runs]), hide_index=True, use_container_width=True)
    if st.button('Đồng bộ BingX 24 giờ vừa qua (chỉ GET)'):
        try:
            from .sync import sync
            with st.spinner('Đang đọc dữ liệu BingX...'):
                result = sync(days=1)
            st.success(f'Đồng bộ xong: {result}')
            st.rerun()
        except Exception as exc:
            st.error(f'Đồng bộ thất bại: {type(exc).__name__}: {str(exc)[:300]}')

with mapping:
    st.subheader('Ghép Entry orderId → Combo')
    st.info('BingX không có trường Combo. Chỉ gán bằng entry orderId ghi nhận tại thời điểm Autotrade nhận xác nhận lệnh; không suy đoán từ thời gian.')
    if links:
        st.dataframe(pd.DataFrame([{'entry_order_id':x.entry_order_id,'combo':x.combo,'symbol':x.symbol,'signal_id':x.signal_id,'source':x.source} for x in links]), hide_index=True, use_container_width=True)
    with st.form('link_form'):
        order_id = st.text_input('Entry orderId từ BingX')
        combo = st.text_input('Combo (ví dụ: COMBO_5_15M)')
        signal_id = st.text_input('Signal ID (tùy chọn)')
        symbol = st.text_input('Symbol (tùy chọn)', value='BTC-USDT')
        if st.form_submit_button('Lưu liên kết Combo'):
            if not order_id.strip().isdigit() or not combo.strip():
                st.error('orderId phải là chuỗi số hợp lệ và Combo không được trống.')
            elif order_id.strip() not in {x.order_id for x in orders}:
                st.warning('OrderId chưa có trong lịch sử BingX. Hãy đồng bộ trước để tránh nhập sai.')
            else:
                with Session.begin() as db:
                    db.merge(SignalLink(entry_order_id=order_id.strip(),combo=combo.strip()[:100],signal_id=signal_id.strip()[:120],
                                        symbol=symbol.strip().upper()[:40],source='manual'))
                st.success('Đã lưu. Tải lại để cập nhật thống kê.')
    st.markdown('**Nhập nhiều Combo từ file CSV**')
    uploaded = st.file_uploader('CSV gồm entry_order_id,combo,signal_id,symbol,side,signal_time_ms', type=['csv'])
    if uploaded is not None and st.button('Nhập CSV'):
        try:
            df = pd.read_csv(io.BytesIO(uploaded.getvalue()), dtype=str).fillna('')
            if not {'entry_order_id','combo'}.issubset(df.columns):
                raise ValueError('CSV phải có cột entry_order_id và combo')
            known = {x.order_id for x in orders}
            missing = []
            with Session.begin() as db:
                for _, row in df.iterrows():
                    oid, combo_name = str(row['entry_order_id']).strip(), str(row['combo']).strip()
                    if oid not in known or not oid.isdigit() or not combo_name:
                        missing.append(oid)
                        continue
                    ts = str(row.get('signal_time_ms', '')).strip()
                    db.merge(SignalLink(entry_order_id=oid, combo=combo_name[:100],
                                        signal_id=str(row.get('signal_id',''))[:120],symbol=str(row.get('symbol',''))[:40],
                                        side=str(row.get('side',''))[:12], signal_time_ms=int(ts) if ts.isdigit() else 0,
                                        source='csv'))
            st.success(f'Đã nhập {len(df)-len(missing)} bản ghi. Bỏ qua {len(missing)} bản ghi chưa khớp orderId.')
        except Exception as exc:
            st.error(f'CSV không hợp lệ: {str(exc)[:300]}')

st.divider()
st.caption('Dashboard chỉ đọc API BingX; không sửa cơ chế giao dịch, không lấy thông tin từ Discord. Số liệu chưa thay thế đối soát tài khoản và chưa phân bổ funding vào Combo.')
