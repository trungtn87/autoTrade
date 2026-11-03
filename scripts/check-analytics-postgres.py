"""Isolated PostgreSQL migration/lock check. No production connections or ports."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NAME = 'autotrade-pg-test-' + uuid.uuid4().hex[:12]
IMAGE = 'postgres:17-alpine@sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b'
ENV = {k:v for k,v in os.environ.items() if k not in {
    'DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'}}
DOCKER = ['docker','--host=unix:///var/run/docker.sock']


def docker(*args, **kwargs):
    result = subprocess.run(DOCKER+list(args), env=ENV, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        # This runner contains synthetic data only; surface actual SQL failures.
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def psql(sql):
    return docker('exec','-i',NAME,'psql','-h','127.0.0.1','-XAtq','-v','ON_ERROR_STOP=1','-U','postgres', input=sql)


socket_dir = tempfile.TemporaryDirectory(prefix='autotrade-pg-socket-')
os.chmod(socket_dir.name, 0o777)
try:
    docker('run','-d','--rm','--network','none','--name',NAME,
           '--mount', f'type=bind,src={socket_dir.name},dst=/test-sockets',
           '-e','POSTGRES_HOST_AUTH_METHOD=trust',IMAGE,
           'postgres','-c','unix_socket_directories=/var/run/postgresql,/test-sockets')
    for _ in range(120):
        try:
            psql('SELECT 1;')
            break
        except RuntimeError:
            time.sleep(.25)
    else:
        raise RuntimeError('Isolated PostgreSQL did not become ready: ' + docker('logs', NAME))
    psql('CREATE ROLE anon; CREATE ROLE authenticated;')
    migration = ROOT/'supabase/migrations/20261103010119_v2_trade_results_draft.sql'
    psql(migration.read_text())
    row = dict(event_id='fixture', entry_order_id='9999999999999999999',
               symbol='BTC-USDT', combo='C1', side='BUY', opened_ms=1000, closed_ms=2000,
               status='CLOSED', outcome=None, exit_types=['TP','TRAILING'],
               opened_qty=2, closed_qty=2, pnl=None, fees=None, margin=None, roi=None,
               source='BINGX_EXACT_ORDER_IDS')
    # JSON values are fixed synthetic fixture data, not runtime/user input.
    data = json.dumps(row).replace("'", "''")
    sql = f"INSERT INTO autotrade_analytics.trade_results(event_id,payload) VALUES ('fixture','{data}'::jsonb) ON CONFLICT(event_id) DO UPDATE SET payload=EXCLUDED.payload;"
    psql(sql)
    psql(sql)
    assert psql('SELECT count(*) FROM autotrade_analytics.trade_results;') == '1'
    assert psql("SELECT status||'|'||opened_qty||'|'||(roi IS NULL)::text FROM autotrade_analytics.trade_results;") == 'CLOSED|2|true'
    # SQL NULL must not let a CLOSED result or non-null ROI bypass validation.
    for bad in (
        "UPDATE autotrade_analytics.trade_results SET payload=payload||'{\"event_id\":null}'::jsonb;",
        "UPDATE autotrade_analytics.trade_results SET payload=payload-'closed_qty';",
        "UPDATE autotrade_analytics.trade_results SET payload=payload||'{\"roi\":1,\"pnl\":1}'::jsonb;",
    ):
        try:
            psql(bad)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Invalid normalized result passed SQL constraints')
    try:
        psql('SET ROLE anon; SELECT * FROM autotrade_analytics.trade_results;')
    except RuntimeError:
        pass
    else:
        raise AssertionError('Anonymous role can read private results')
    # Baseline snapshot survives additive migrations byte-for-byte at revision 0.
    original = psql('SELECT payload::text FROM autotrade_analytics.trade_results;')
    for extra in sorted((ROOT/'supabase/migrations').glob('*.sql')):
        if extra != migration:
            psql(extra.read_text())
    assert psql('SELECT payload::text FROM autotrade_analytics.trade_results;') == original
    assert psql('SELECT revision FROM autotrade_analytics.trade_results;') == '0'
    from sqlalchemy import create_engine
    from check_result_storage import verify_storage
    engine = create_engine('postgresql+psycopg://postgres@/postgres',
                           connect_args={'host':socket_dir.name, 'connect_timeout':3})
    try:
        verify_storage(engine)
    finally:
        engine.dispose()
    # Hold a transaction on one connection; another must be excluded. Closing
    # the holder connection simulates a killed collector and releases its lock.
    holder = subprocess.Popen(DOCKER+['exec','-i',NAME,'psql','-h','127.0.0.1','-XAtq','-U','postgres'],
                              env=ENV, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    holder.stdin.write("BEGIN; SET LOCAL idle_in_transaction_session_timeout='0'; SELECT pg_try_advisory_xact_lock(2707,20261027);\n")
    holder.stdin.flush()
    assert holder.stdout.readline().strip() == 't'
    assert psql('SELECT pg_try_advisory_xact_lock(2707,20261027);') == 'f'
    holder.stdin.close()
    holder.wait(timeout=10)
    assert psql('SELECT pg_try_advisory_xact_lock(2707,20261027);') == 't'
    print('PASS: isolated PostgreSQL 17 migration, idempotent event key, NULL constraints, private access, collector exclusion and lock recovery')
finally:
    subprocess.run(DOCKER+['rm','-f',NAME], env=ENV, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    socket_dir.cleanup()
