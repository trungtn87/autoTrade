"""Real Chromium mobile/PWA checks against an isolated fixture API, never production."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8765'


def serve():
    from types import SimpleNamespace as Row
    from autotrade_live import mobile
    from autotrade_live.exits import extract_exit_spec
    import uvicorn

    records = {k: [] for k in ('orders', 'fills', 'links', 'forces', 'incomes', 'positions', 'runs')}
    states = []
    for i in range(24):
        ts = int(time.time()*1000) - 86400000 + i * 100000
        combo = i % 2 + 1
        coin = 'ETH-USDT' if i == 2 else 'BTC-USDT'
        entry, sl, tp, trail = [str(1000 + i*10 + j) for j in range(4)]
        state = dict(intent=dict(symbol=coin, combo=combo, side='BUY',
                                 close_time=ts, entry=100, tp=101, sl=99),
                     entry_filled=i < 3, entry_accepted=i < 3, terminal=True,
                     order_id=entry if i < 3 else '', avg_price=100,
                     exit_mode='split_trailing', executed_qty=1,
                     protection={'sl': {'order_id': sl}, 'tp': {'order_id': tp},
                                 'trailing': {'order_id': trail}})
        states.append((f'fixture-{i}', state))
        if i >= 3:
            continue
        for j, (oid, kind, price, qty, side) in enumerate([
            (entry, 'MARKET', 100, 1, 'BUY'),
            (tp, 'TAKE_PROFIT_MARKET', 101, .5, 'SELL'),
            (trail, 'TRAILING_STOP_MARKET', 102, .5, 'SELL'),
        ]):
            records['orders'].append(Row(order_id=oid, symbol=coin, side=side,
                position_side='LONG', type=kind, status='FILLED', price=price,
                avg_price=price, quantity=qty, filled_qty=qty, created_ms=ts+j*1000,
                updated_ms=ts+j*1000, raw={}))
            records['fills'].append(Row(order_id=oid, trade_id=f'f-{oid}', symbol=coin,
                side=side, position_side='LONG', qty=qty, price=price,
                realized_pnl=(price-100)*qty, fee=-.01, timestamp_ms=ts+j*1000, raw={}))
    records['v2_states'] = states
    records['exit_specs'] = [x for eid, state in states if (x := extract_exit_spec(state, eid))]
    mobile._db_records = lambda: records
    mobile.archive_enabled = lambda: False
    uvicorn.run(mobile.app, host='127.0.0.1', port=8765)


def check():
    from playwright.sync_api import sync_playwright
    env = {k: v for k, v in os.environ.items() if not any(word in k.upper() for word in
           ('BINGX', 'DATABASE_URL', 'DASHBOARD_', 'APP_SESSION', 'COLLECTOR_', 'DISCORD', 'WEBHOOK'))}
    env.update(PYTHONPATH=str(ROOT/'dashboard'), DASHBOARD_PUBLIC_READONLY='false',
               DASHBOARD_PASSWORD='local-fixture-password',
               APP_SESSION_SECRET='local-fixture-session-secret-at-least-32-characters', COOKIE_SECURE='false')
    proc = subprocess.Popen([sys.executable, __file__, '--serve'], cwd=ROOT, env=env)
    output = ROOT/'browser-evidence'
    output.mkdir(exist_ok=True)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE+'/health', timeout=1)
                break
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError('Fixture server exited')
                time.sleep(.1)
        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for width, height in [(390, 844), (360, 800), (1280, 900)]:
                context = browser.new_context(viewport={'width': width, 'height': height},
                                             is_mobile=width < 600, has_touch=width < 600)
                context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(BASE+'/') else route.abort())
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda err: errors.append(str(err)))
                page.goto(BASE+'/mobile')
                assert '/login' in page.url
                page.locator('input[name=password]').fill('local-fixture-password')
                page.locator('button').click()
                page.wait_for_function("document.querySelector('#history-count').textContent.includes('3 lệnh')")
                # Never await navigator.serviceWorker.ready indefinitely.
                page.wait_for_function('navigator.serviceWorker.controller !== null', timeout=10000)
                assert page.evaluate('navigator.serviceWorker.controller.scriptURL.endsWith("/sw.js")')
                assert page.request.get(BASE+'/sw.js').headers['service-worker-allowed'] == '/'
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'horizontal page overflow'
                # Fixture: BTC x2 + ETH x1, with three confirmed closes on one date.
                payload = page.request.get(BASE+'/api/dashboard?days=all').json()
                assert payload['theory']['summary']['closed'] == 3
                assert payload['theory']['summary']['failed'] == 21
                assert len(payload['theory']['history']) == 3
                assert {row['symbol'] for row in payload['theory']['history']} == {'BTC-USDT','ETH-USDT'}
                assert all(row['status'] == 'closed' and row['broker_entry_verified']
                           for row in payload['theory']['history'])
                assert page.locator('#history-list tr').count() == 3
                assert page.locator('#history-list td.coin-btc').count() == 2
                assert page.locator('#history-list td.coin-eth').count() == 1
                assert page.locator('#combo-list tr').count() == 2  # BTC only
                assert page.locator('#calendar-grid .has-trades').count() >= 1
                assert page.locator('#day-wins').inner_text() == '3'
                assert page.locator('#day-losses').inner_text() == '0'

                # BTC/ETH toggle affects only the Combo table, never the shared calendar.
                date_before = page.locator('#selected-date').inner_text()
                page.locator('#tab-eth').click()
                assert page.locator('#combo-list tr').count() == 1
                assert page.locator('#history-list tr').count() == 3
                assert page.locator('#selected-date').inner_text() == date_before
                assert page.locator('#history-count').inner_text() == '3 lệnh'
                page.locator('#tab-btc').click()
                assert page.locator('#combo-list tr').count() == 2
                assert page.locator('#history-list tr').count() == 3

                # Selecting any empty date filters rows to zero for both coins.
                empty = page.locator('#calendar-grid .calendar-day:not(.has-trades):not([disabled])')
                assert empty.count() > 0
                empty.first.click()
                assert page.locator('#history-count').inner_text() == '0 lệnh'
                assert page.locator('#day-wins').inner_text() == '0'
                assert page.locator('#day-losses').inner_text() == '0'
                page.locator('#calendar-grid .has-trades').first.click()
                assert page.locator('#history-list tr').count() == 3
                manifest = page.request.get(BASE+'/static/manifest.webmanifest').json()
                assert manifest['display'] == 'standalone'
                assert not errors, errors
                page.screenshot(path=str(output/f'mobile-{width}.png'), full_page=True)
                results.append(dict(width=width, height=height, history=3,
                                    coins=['BTC','ETH'], shared_calendar=True,
                                    combo_filter=True, service_worker=True,
                                    javascript_errors=errors))
                context.close()
            browser.close()
        (output/'result.json').write_text(json.dumps(results, indent=2))
        print(json.dumps(results))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == '__main__':
    serve() if '--serve' in sys.argv else check()
