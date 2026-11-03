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
    from autotrade_live import mobile
    import uvicorn

    # Synthetic persisted-result fixtures, not a reconstruction from strategy
    # prices. Include a money-unverified CLOSED trade to guard NULL rendering.
    rows = []
    for i, (coin, combo, outcome, roi) in enumerate([
        ('BTC-USDT', 'C1', 'WIN', 1.2),
        ('BTC-USDT', 'C2', 'LOSS', -0.5),
        ('ETH-USDT', 'C1', None, None),
    ]):
        ts = int(time.time()*1000) - 86400000 + i * 100000
        rows.append(dict(event_id=f'fixture-{i}', symbol=coin, combo=combo,
                         side='BUY', status='CLOSED', outcome=outcome, roi=roi,
                         opened_ms=ts, closed_ms=ts+2000,
                         source='SYNTHETIC_DB_FIXTURE', exit_types=['TP','TRAILING']))
    mobile._db_records = lambda: {'normalized_results': rows, 'last_sync':None}
    uvicorn.run(mobile.app, host='127.0.0.1', port=8765)


def check():
    from playwright.sync_api import sync_playwright
    env = {k: v for k, v in os.environ.items() if not any(word in k.upper() for word in
           ('BINGX', 'DATABASE_URL', 'DASHBOARD_', 'APP_SESSION', 'COLLECTOR_', 'DISCORD', 'WEBHOOK'))}
    env.update(PYTHONPATH=os.pathsep.join([str(ROOT),str(ROOT/'dashboard')]), DASHBOARD_PUBLIC_READONLY='false',
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
                assert payload['summary']['closed'] == 3
                assert len(payload['theory']['history']) == 3
                assert {row['symbol'] for row in payload['theory']['history']} == {'BTC-USDT','ETH-USDT'}
                assert all(row['status'] == 'closed' and row['source'] == 'SYNTHETIC_DB_FIXTURE'
                           for row in payload['theory']['history'])
                assert page.locator('#history-list tr').count() == 3
                assert page.locator('#history-list td.coin-btc').count() == 2
                assert page.locator('#history-list td.coin-eth').count() == 1
                assert page.locator('#combo-list tr').count() == 2  # BTC only
                assert page.locator('#calendar-grid .has-trades').count() >= 1
                assert page.locator('#day-wins').inner_text() == '1'
                assert page.locator('#day-roi').inner_text() == '—'
                assert 'CHƯA XÁC MINH' in page.locator('#history-list').inner_text()
                assert page.locator('#day-losses').inner_text() == '1'

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
