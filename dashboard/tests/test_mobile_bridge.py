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


def test_mobile_rejects_unauthenticated():
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
