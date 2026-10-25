import json
import os
from types import SimpleNamespace
from fastapi.testclient import TestClient
from autotrade_live.bridge import extract_link, case_name
from autotrade_live.mobile import app, dashboard_payload


def test_extract_from_realistic_v2_state():
    payload={'entry_filled':True, 'order_id':'1234567890',
             'intent':{'combo':103,'symbol':'ETH-USDT','side':'BUY','close_time':1790000000000}}
    x=extract_link(json.dumps(payload),'signal-aaa')
    assert x['entry_order_id']=='1234567890'
    assert x['combo']=='C13'
    assert x['signal_id']=='signal-aaa'
    assert extract_link({**payload,'entry_filled':False}) is None
    assert case_name(11)=='TIER'


def test_mobile_rejects_unauthenticated(monkeypatch):
    monkeypatch.delenv('DASHBOARD_PUBLIC_READONLY', raising=False)
    with TestClient(app) as c:
        assert c.get('/api/dashboard').status_code==401
        assert c.get('/mobile',follow_redirects=False).status_code==303
        assert c.get('/health').json()['trade_api'] is False


def test_mobile_login_cookie_and_private_api(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'mobile.db'))
    monkeypatch.setenv('DB_SCHEMA','')
    monkeypatch.setenv('DASHBOARD_PASSWORD','private-test-password-1234')
    monkeypatch.setenv('APP_SESSION_SECRET','long-session-secret-over-32-characters-test')
    monkeypatch.setenv('COOKIE_SECURE','false')
    monkeypatch.delenv('DASHBOARD_PUBLIC_READONLY', raising=False)
    from autotrade_live.db import get_engine,init_db
    init_db(get_engine())
    with TestClient(app) as c:
        assert c.post('/login',data={'password':'wrong'},follow_redirects=False).status_code==401
        r=c.post('/login',data={'password':'private-test-password-1234'},follow_redirects=False)
        assert r.status_code==303
        assert 'httponly' in r.headers['set-cookie'].lower()
        assert c.get('/mobile').status_code==200
        r=c.get('/api/dashboard')
        assert r.status_code==200
        assert r.headers['cache-control']=='no-store, private'
        assert r.json()['summary']['closed']==0
        assert c.post('/logout',follow_redirects=False).status_code==303
        assert c.get('/api/dashboard').status_code==401


def test_payload_zero_is_honest():
    no={'orders':[],'fills':[],'links':[],'forces':[],'incomes':[],'runs':[],'positions':[]}
    p=dashboard_payload(no)
    assert p['summary']['pnl_usdt']==0
    assert p['trades']==[]


def test_public_readonly_no_password_and_redacted_api(monkeypatch):
    # Public mode must not require the password or session signing key.
    monkeypatch.setenv('DASHBOARD_PUBLIC_READONLY', 'true')
    monkeypatch.delenv('DASHBOARD_PASSWORD', raising=False)
    monkeypatch.delenv('APP_SESSION_SECRET', raising=False)
    from autotrade_live import mobile as web
    records = {'orders': [], 'fills': [], 'links': [], 'forces': [],
               'incomes': [], 'runs': [], 'positions': []}
    monkeypatch.setattr(web, '_db_records', lambda: records)
    with TestClient(app) as c:
        assert c.get('/', follow_redirects=False).headers['location'] == '/mobile'
        assert c.get('/login', follow_redirects=False).headers['location'] == '/mobile'
        assert c.post('/login', data={'password': 'none'}, follow_redirects=False).headers['location'] == '/mobile'
        page = c.get('/mobile')
        assert page.status_code == 200
        assert 'Đăng xuất' not in page.text
        assert 'Chỉ xem' in page.text
        data = c.get('/api/dashboard')
        assert data.status_code == 200
        assert data.json()['summary']['closed'] == 0
        assert data.headers['x-robots-tag'].startswith('noindex')
        assert c.post('/collector/v1/signals', json={'entry_order_id': '123', 'combo': 'C1'}).status_code == 401
        assert c.post('/logout', follow_redirects=False).headers['location'] == '/mobile'


def test_public_readonly_strips_trade_ids_and_sync_details(monkeypatch):
    monkeypatch.setenv('DASHBOARD_PUBLIC_READONLY', 'true')
    from autotrade_live import mobile as web
    monkeypatch.setattr(web, '_db_records', lambda: {'dummy': True})
    monkeypatch.setattr(web, 'dashboard_payload', lambda *a, **kw: {
        'trades': [{'symbol': 'BTC-USDT', 'combo': 'C1', 'pnl': 1.5,
                    'order_id': 'sensitive123', 'raw': {'private': True}}],
        'last_sync': {'status': 'success', 'details': 'private internal error text'},
        'summary': {'pnl_usdt': 1.5},
    })
    with TestClient(app) as c:
        res = c.get('/api/dashboard')
        assert res.status_code == 200
        assert res.json()['trades'][0]['symbol'] == 'BTC-USDT'
        assert 'order_id' not in res.json()['trades'][0]
        assert 'raw' not in res.json()['trades'][0]
        assert 'details' not in res.json()['last_sync']


def test_baseline_starts_at_entry_not_exit(monkeypatch):
    from autotrade_live import mobile as web
    monkeypatch.setenv('DASHBOARD_START_AT','2026-10-25T17:40:50+07:00')
    base=int(__import__('datetime').datetime.fromisoformat('2026-10-25T17:40:50+07:00').timestamp()*1000)
    from types import SimpleNamespace as O
    def row(i,ts,oid,side,pnl,fee=0):
        return O(trade_id=str(i),timestamp_ms=ts,order_id=oid,symbol='BTC-USDT',
                 side=side,position_side='LONG',qty=1,price=100,realized_pnl=pnl,fee=fee)
    orders=[O(order_id=x,type='MARKET',position_side='LONG',raw={'leverage':'50X','positionID':pos})
            for x,pos in [('old_in','old'),('old_out','old'),('new_in','new'),('new_out','new')]]
    rows=[row('1',base-5000,'old_in','BUY',0),
          row('2',base+1000,'old_out','SELL',50),
          row('3',base+2000,'new_in','BUY',0),
          row('4',base+3000,'new_out','SELL',10)]
    records=dict(fills=rows,orders=orders,links=[O(entry_order_id='old_in',combo='C1'),
                 O(entry_order_id='new_in',combo='C2')],forces=[],incomes=[],runs=[],positions=[])
    result=web.dashboard_payload(records)
    assert result['summary']['pnl_usdt']==10
    assert result['summary']['closed']==1
    assert result['summary']['pnl_pct']==500
    assert result['combos'][0]['Combo']=='C2'
    assert result['start_at'].startswith('2026-10-25T10:40:50')
    monkeypatch.delenv('DASHBOARD_START_AT',raising=False)


def test_public_api_percent_kept_private_trade_ids_rejected(monkeypatch):
    from autotrade_live import mobile as web
    monkeypatch.setenv('DASHBOARD_PUBLIC_READONLY','true')
    monkeypatch.delenv('DASHBOARD_START_AT',raising=False)
    monkeypatch.setattr(web,'_db_records',lambda:{'anything':True})
    monkeypatch.setattr(web,'dashboard_payload',lambda *a,**kw:{
        'trades':[{'symbol':'ETH-USDT','combo':'C8','pnl':4,'pnl_pct':200,
                   'order_id':'secret-id'}], 'last_sync':None,'summary':{'pnl_pct':200}})
    with TestClient(app) as c:
        x=c.get('/api/dashboard')
        assert x.status_code==200
        assert x.json()['trades'][0]['pnl_pct']==200
        assert 'order_id' not in x.json()['trades'][0]
