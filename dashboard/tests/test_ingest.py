from fastapi.testclient import TestClient
from autotrade_live.ingest import app


def test_collector_requires_auth(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL','sqlite:///' + str(tmp_path/'collector.db'))
    monkeypatch.setenv('COLLECTOR_TOKEN','test-token')
    client = TestClient(app)
    event={'entry_order_id':'12345678990123456789','combo':'C5','symbol':'ETH-USDT','side':'BUY'}
    assert client.post('/v1/signals',json=event).status_code == 401
    assert client.post('/v1/signals',json=event,headers={'X-Collector-Token':'test-token'}).status_code == 202
    assert client.post('/v1/signals',json=event,headers={'X-Collector-Token':'test-token'}).status_code == 202
    event['combo']='C3'
    assert client.post('/v1/signals',json=event,headers={'X-Collector-Token':'test-token'}).status_code == 409
