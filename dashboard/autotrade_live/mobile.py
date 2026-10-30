"""HTTPS/PWA read-only dashboard and optional Combo receiver, separate from trading bot."""
from __future__ import annotations
import asyncio
import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .analytics import build_trades, margin_roi, summarize
from .exits import load_exit_specs, build_exit_events
from .theory import load_states, build_theory
from .db import (Fill, ForceOrder, Income, Order, PositionHistory,
                 SignalLink, SyncRun, get_engine, get_sessionmaker, init_db)
from .ingest import app as ingest_app
from .web_scheduler import archive_enabled, archive_scheduler_loop


@asynccontextmanager
async def dashboard_lifespan(_app):
    # Always on for this read-only dashboard; no extra enable flag needed.
    # Existing wake-up script only needs the /health route.
    worker = asyncio.create_task(archive_scheduler_loop()) if archive_enabled() else None
    try:
        yield
    finally:
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


STATIC = Path(__file__).resolve().parent / 'static'
app = FastAPI(title='Autotrade Live Dashboard PWA', docs_url=None, redoc_url=None,
              lifespan=dashboard_lifespan)
app.mount('/static', StaticFiles(directory=str(STATIC)), name='static')
app.mount('/collector', ingest_app)

@app.get('/sw.js', include_in_schema=False)
def service_worker():
    # A PWA installed at /mobile needs a worker whose scope covers /mobile.
    # Serving from /static/sw.js would only control /static/ and leave
    # navigator.serviceWorker.ready unresolved on the main Dashboard page.
    return FileResponse(
        STATIC / 'sw.js',
        media_type='application/javascript',
        headers={
            'Service-Worker-Allowed': '/',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
        },
    )

ATTEMPTS: dict[str, list[float]] = defaultdict(list)
COOKIE = 'autotrade_dashboard_session'


def _public_readonly():
    """Opt-in passwordless access: anyone with the URL can read summary/trades."""
    return os.environ.get('DASHBOARD_PUBLIC_READONLY', '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _secrets():
    password = os.environ.get('DASHBOARD_PASSWORD', '')
    key = os.environ.get('APP_SESSION_SECRET', '')
    if len(password) < 12 or len(key) < 32:
        raise RuntimeError('DASHBOARD_PASSWORD (12+) and APP_SESSION_SECRET (32+) required')
    return password, key.encode()


def _session_token():
    _, key = _secrets()
    ts = str(int(time.time()))
    nonce = secrets.token_hex(12)
    msg = f'{ts}:{nonce}'
    sig = hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f'{msg}:{sig}'.encode()).decode()


def _authenticated(request):
    raw = request.cookies.get(COOKIE, '')
    if not raw:
        return False
    try:
        decoded = base64.urlsafe_b64decode(raw.encode()).decode()
        ts, nonce, sig = decoded.split(':', 2)
        if int(time.time()) - int(ts) > 86400 or int(ts) > int(time.time()) + 60:
            return False
        _, key = _secrets()
        expected = hmac.new(key, f'{ts}:{nonce}'.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except (ValueError, UnicodeError, RuntimeError):
        return False


def _require_auth(request):
    if not _public_readonly() and not _authenticated(request):
        raise HTTPException(status_code=401, detail='Login required')


def _no_cache(response):
    response.headers['Cache-Control'] = 'no-store, private'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.get('/health')
def health():
    # Configuration flag only; no credentials, BingX data, or order APIs.
    return {'ok': True, 'service': 'autotrade-dashboard', 'trade_api': False,
            'archive_scheduler_enabled': archive_enabled()}


@app.get('/')
def home(request: Request):
    return RedirectResponse('/mobile' if _public_readonly() or _authenticated(request) else '/login', status_code=303)


@app.get('/login', response_class=HTMLResponse)
def login_form(request: Request):
    if _public_readonly() or _authenticated(request):
        return RedirectResponse('/mobile', status_code=303)
    html = '''<!DOCTYPE html><html lang="vi"><head><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta charset="UTF-8"><title>Autotrade • Đăng nhập</title><link rel="manifest" href="/static/manifest.webmanifest">
    <style>body{font:16px system-ui;background:#0d1220;color:#f2f7fc;margin:0;display:grid;place-items:center;min-height:100vh}
    form{width:min(350px,82vw);padding:30px;background:#192339;border-radius:22px;border:1px solid #35435e}
    input,button{width:100%;padding:15px;box-sizing:border-box;border-radius:10px;font-size:16px;margin-top:15px}
    input{background:#0c1527;color:white;border:1px solid #51617b}button{background:#3fd9b1;color:#09221b;border:0;font-weight:800}</style></head>
    <body><form action="/login" method="post"><h2>◈ AUTOTRADE</h2><p>Dashboard giao dịch thật</p>
    <input name="password" type="password" placeholder="Mật khẩu" autocomplete="current-password" required>
    <button>Đăng nhập</button></form></body></html>'''
    return _no_cache(HTMLResponse(html))


@app.post('/login')
def login(request: Request, password: str = Form('')):
    if _public_readonly():
        return RedirectResponse('/mobile', status_code=303)
    expected, _ = _secrets()
    identity = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    ATTEMPTS[identity] = [x for x in ATTEMPTS[identity] if now - x < 900]
    if len(ATTEMPTS[identity]) >= 8:
        raise HTTPException(status_code=429, detail='Too many attempts')
    if not hmac.compare_digest(password, expected):
        ATTEMPTS[identity].append(now)
        return _no_cache(HTMLResponse('Sai mật khẩu. <a href="/login">Thử lại</a>', status_code=401))
    ATTEMPTS.pop(identity, None)
    response = RedirectResponse('/mobile', status_code=303)
    response.set_cookie(COOKIE, _session_token(), max_age=86400,
                        httponly=True, secure=os.getenv('COOKIE_SECURE', 'true').lower() != 'false',
                        samesite='lax', path='/')
    return _no_cache(response)


@app.post('/logout')
def logout():
    r = RedirectResponse('/mobile' if _public_readonly() else '/login', status_code=303)
    r.delete_cookie(COOKIE, path='/')
    return _no_cache(r)


@app.get('/mobile')
def mobile(request: Request):
    if not _public_readonly() and not _authenticated(request):
        return RedirectResponse('/login', status_code=303)
    html = (STATIC / 'index.html').read_text('utf-8')
    if _public_readonly():
        # The public viewer cannot log out; the old control would mislead users.
        html = html.replace('<form method="post" action="/logout"><button class="quiet">Đăng xuất</button></form>',
                            '<span class="quiet" title="Dữ liệu công khai, chỉ đọc">Chỉ xem</span>')
    return _no_cache(HTMLResponse(html))


def _db_records():
    engine = get_engine()
    Session = get_sessionmaker(engine)
    with Session() as db:
        records = dict(
            orders=db.scalars(select(Order)).all(),
            fills=db.scalars(select(Fill).order_by(Fill.timestamp_ms, Fill.trade_id)).all(),
            incomes=db.scalars(select(Income)).all(),
            forces=db.scalars(select(ForceOrder)).all(),
            positions=db.scalars(select(PositionHistory)).all(),
            links=db.scalars(select(SignalLink)).all(),
            runs=db.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(1)).all(),
        )
    # Read only already-persisted trade IDs, never call BingX from dashboard.
    records['exit_specs'] = load_exit_specs(engine)
    records['v2_states'] = load_states(engine)
    return records


def _start_ms():
    """Count only positions opened on/after this fixed, timezone-aware date."""
    start_at = os.getenv('DASHBOARD_START_AT', '').strip()
    if not start_at:
        return 0
    dt = datetime.fromisoformat(start_at.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('DASHBOARD_START_AT must include a UTC offset')
    return int(dt.timestamp() * 1000)


def dashboard_payload(records, symbol=None, combo=None, days='all'):
    start_ms = _start_ms()
    completed, pending, unresolved = build_trades(records['fills'], records['orders'], records['links'], records['forces'])
    all_combos = sorted({x.combo for x in records['links']} |
                        {x['combo'] for x in records.get('exit_specs', [])})
    all_symbols = sorted({getattr(x,'symbol','') for x in records['orders'] + records['fills'] if getattr(x,'symbol','')})
    # Count only whole positions whose first ENTRY is at/after the baseline.
    # An old position closed today never becomes a new-period profit.
    completed = [x for x in completed if x['entry_ms'] >= start_ms and
                 (not symbol or x['symbol'] == symbol)]
    pending = [x for x in pending if x['entry_ms'] >= start_ms and
               (not symbol or x['symbol'] == symbol)]
    visible = [x for x in completed if not combo or x['combo'] == combo]
    mapped = [x for x in visible if x['combo']]
    total = round(sum(x['pnl'] for x in mapped), 6)
    roi_pct, margin = margin_roi(mapped)
    chart, running = [], 0.0
    for trade in sorted(mapped, key=lambda x:x['exit_ms'] or 0):
        running += trade['pnl']
        chart.append({'t': trade['exit_ms'], 'v': round(running, 6)})
    liqs = sum(1 for x in visible if x['liquidated'])

    exit_events = [
        x for x in build_exit_events(records['orders'], records['fills'],
                                     records.get('exit_specs', []), records['forces'])
        if x['time_ms'] >= start_ms and (not symbol or x['symbol'] == symbol)
        and (not combo or x['combo'] == combo)
    ]
    # BingX exit PnL may be based on a shared average-price position.
    confirmed_exit_events = [x for x in exit_events if x['confidence'] in ('ORDER_ID','TRIGGER_ORDER_ID','LEGACY_SL_CLIENT_ID')]
    confirmed_by_combo = defaultdict(lambda: {'combo':'','count':0,'TP':0,'SL':0,'TRAILING':0,
                                              'realized_pnl':0.0,'exit_fees':0.0})
    for row in confirmed_exit_events:
        if not row['combo']:
            continue
        item = confirmed_by_combo[row['combo']]
        item['combo'] = row['combo']
        item['count'] += 1
        if row['type'] in ('TP','SL','TRAILING'):
            item[row['type']] += 1
        item['realized_pnl'] += row['bingx_realized_pnl']
        item['exit_fees'] += row['exit_fees']
    combo_results = defaultdict(lambda: {'wins': 0, 'losses': 0, 'net': 0.0})
    running_exit_net = 0.0
    exit_curve = []
    wins = losses = breakeven = 0
    gross_winners = gross_losers = 0.0
    for event in sorted(confirmed_exit_events, key=lambda x: (x['time_ms'], x['order_id'])):
        # This is ONLY realizedPnL from a BingX exit and its associated
        # exit-order fee. Entry fees and funding are not attributed here.
        value = event['bingx_realized_pnl'] + event['exit_fees']
        if value > 1e-9:
            wins += 1
            gross_winners += value
        elif value < -1e-9:
            losses += 1
            gross_losers += abs(value)
        else:
            breakeven += 1
        row = combo_results[event['combo']]
        row['net'] += value
        row['wins'] += int(value > 1e-9)
        row['losses'] += int(value < -1e-9)
        running_exit_net += value
        exit_curve.append({'t': event['time_ms'], 'v': round(running_exit_net, 6)})
    exit_performance = {
        'count': len(confirmed_exit_events),
        'wins': wins,
        'losses': losses,
        'breakeven': breakeven,
        'win_rate': round(100 * wins / len(confirmed_exit_events), 2) if confirmed_exit_events else None,
        'pnl_usdt': round(running_exit_net, 6),
        # Null instead of infinite when there are no confirmed losing exits.
        'profit_factor': round(gross_winners / gross_losers, 2) if gross_losers > 1e-9 else None,
        'curve': exit_curve,
    }
    combo_exits = []
    for item in sorted(confirmed_by_combo.values(), key=lambda x: (
            -combo_results[x['combo']]['net'], x['combo'])):
        item['realized_pnl'] = round(item['realized_pnl'], 6)
        item['exit_fees'] = round(item['exit_fees'], 6)
        item['net_after_exit_fee'] = round(combo_results[item['combo']]['net'], 6)
        item['win_rate'] = round(100 * combo_results[item['combo']]['wins'] / item['count'], 2)
        combo_exits.append(item)
    exit_summary = {
        'matched_by_id': len(confirmed_exit_events),
        'matched_by_trigger': sum(x['confidence'] == 'TRIGGER_ORDER_ID' for x in confirmed_exit_events),
        'matched_by_legacy_client': sum(x['confidence'] == 'LEGACY_SL_CLIENT_ID' for x in confirmed_exit_events),
        'price_time_candidates': sum(x['confidence'] == 'PRICE_TIME_CANDIDATE' for x in exit_events),
        'unmapped_or_ambiguous': sum(x['confidence'] in ('UNMAPPED','AMBIGUOUS','CONFLICT') for x in exit_events),
        'bingx_exit_realized_usdt': round(sum(x['bingx_realized_pnl'] for x in confirmed_exit_events), 6),
        'bingx_exit_fees_usdt': round(sum(x['exit_fees'] for x in confirmed_exit_events), 6),
    }
    # Persisted execution warnings only: dashboard never reads BingX directly.
    # Keep unresolved legacy SL replacements visible even when old cleanup
    # state was marked "done" under an earlier implementation.
    cleanup_risks = []
    for spec in records.get('exit_specs', []):
        if (spec['signal_time_ms'] < start_ms or
                (symbol and spec['symbol'] != symbol) or
                (combo and spec['combo'] != combo)):
            continue
        flags = list(spec.get('cleanup_warnings') or [])
        if spec.get('legacy_sl_pending') and 'LEGACY_SL_REPLACEMENT_UNVERIFIED' not in flags:
            flags.append('LEGACY_SL_REPLACEMENT_UNVERIFIED')
        for flag in flags:
            if flag == 'LEGACY_SL_REPLACEMENT_UNVERIFIED' or spec.get('cleanup_status') != 'done':
                cleanup_risks.append({
                    'combo': spec['combo'],
                    'symbol': spec['symbol'],
                    'reason': flag,
                    'cleanup_status': spec.get('cleanup_status') or 'not_checked',
                })
    cleanup_risks = cleanup_risks[:30]
    fill_net = sum(float(f.realized_pnl) + float(f.fee)
                   for f in records['fills'] if f.timestamp_ms >= start_ms and
                   (not symbol or f.symbol == symbol))
    try:
        period_days = int(days) if days != 'all' else 0
    except (ValueError, TypeError):
        period_days = 0
    period_days = period_days if period_days in (1, 7, 30, 90) else 0
    since_ms = int(time.time() * 1000) - period_days * 86400000 if period_days else 0
    theory = build_theory(records.get('v2_states'), records.get('exit_specs', []),
                          records['orders'], exit_events, start_ms=start_ms,
                          symbol=symbol or '', combo=combo or '', since_ms=since_ms,
                          fills=records['fills'])
    return {
        'theory': theory,
        'theory_since': datetime.fromtimestamp(max(start_ms, since_ms)/1000, timezone.utc).strftime('%d/%m/%Y') if max(start_ms, since_ms) else 'đầu dữ liệu',
        'summary': {
            'pnl_usdt': total,
            'pnl_pct': roi_pct,
            'margin_used_usdt': margin,
            'win_rate': round(100 * sum(x['pnl'] > 0 for x in mapped) / len(mapped), 2) if mapped else 0.0,
            'closed': len(mapped),
            'unmapped': sum(not x['combo'] for x in completed),
            'liquidations': liqs,
            'open_positions': len(pending),
            'fill_net_usdt': round(fill_net, 6),
            'unresolved_fills': sum(1 for x in unresolved if x.get('timestamp_ms', 0) >= start_ms),
        },
        'combos': summarize(mapped),
        'trades': sorted(visible, key=lambda x:x['exit_ms'] or 0, reverse=True)[:100],
        'exit_events': exit_events[:100],
        'exit_summary': exit_summary,
        'combo_exits': combo_exits,
        'exit_performance': exit_performance,
        'cleanup_risks': cleanup_risks,
        'equity': chart,
        'filters': {'combos': all_combos, 'symbols': all_symbols},
        'start_at': datetime.fromtimestamp(start_ms/1000, timezone.utc).isoformat() if start_ms else None,
        'last_sync': ({'status':records['runs'][0].status,
                       'time':records['runs'][0].finished_at.isoformat() if records['runs'][0].finished_at else None,
                       'details':records['runs'][0].details} if records['runs'] else None),
        'disclaimer': 'PnL gồm realisedPNL và phí fills, chưa phân bổ funding. PnL % = PnL ròng / tổng (giá trị Entry / đòn bẩy). Đây là ROI ký quỹ ước tính, không phải lợi nhuận tài khoản; có thể khác ký quỹ thực do thêm margin và cơ chế thanh lý.'
    }


@app.get('/api/dashboard')
def api_dashboard(request: Request, symbol: str = '', combo: str = '', days: str = '30'):
    _require_auth(request)
    try:
        records = _db_records()
        data = dashboard_payload(records, symbol=symbol, combo=combo, days=days)
        if _public_readonly():
            # Public summaries do not expose exchange order IDs, raw fills or
            # collector error details (which can contain internal infrastructure).
            public_fields = ('symbol', 'combo', 'side', 'entry_ms', 'exit_ms', 'exit_types', 'pnl', 'pnl_pct', 'liquidated')
            data['trades'] = [{k: trade.get(k) for k in public_fields} for trade in data['trades']]
            exit_public_fields = ('symbol','combo','type','confidence','time_ms','qty',
                                  'exit_price','bingx_realized_pnl','exit_fees')
            data['exit_events'] = [
                {k: row.get(k) for k in exit_public_fields} for row in data.get('exit_events', [])
            ]
            if data['last_sync']:
                data['last_sync'].pop('details', None)
        return _no_cache(JSONResponse(data))
    except Exception:
        # Avoid leaking database URLs, credentials, or raw data in error responses.
        return _no_cache(JSONResponse({'error': 'Không đọc được dữ liệu. Kiểm tra collector/database.'}, status_code=503))
