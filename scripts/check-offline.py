"""Run every V2 script or the complete dashboard suite without credentials/network."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
service = sys.argv[1]
if service not in {'autotrade_v2', 'dashboard'}:
    raise SystemExit('Expected autotrade_v2 or dashboard')
env = {k: v for k, v in os.environ.items() if not any(
    word in k.upper() for word in ('BINGX', 'DATABASE_URL', 'DISCORD', 'SMTP', 'EMAIL_', 'WEBHOOK', 'DASHBOARD_', 'APP_SESSION', 'COLLECTOR_'))}
env.update(BOOTSTRAP_ENABLED='false', WEBSOCKET_ENABLED='false',
           EXECUTION_ENABLED='false', DRY_RUN='true', DISCORD_ENABLED='false',
           EMAIL_ALERTS_ENABLED='false', DB_SCHEMA='', COOKIE_SECURE='false',
           PYTHONPATH=os.pathsep.join(map(str, [root/'scripts/ci', root/service, root])))
with tempfile.TemporaryDirectory() as tmp:
    env['STATE_DB'] = str(Path(tmp)/'state.db')
    env['DATABASE_URL'] = '' if service == 'autotrade_v2' else 'sqlite:///'+str(Path(tmp)/'dashboard.db')
    commands = []
    if service == 'autotrade_v2':
        commands += [[sys.executable, str(p)] for p in sorted((root/service/'tests').glob('*_test.py'))]
        commands += [[sys.executable, '-m', 'unittest', 'discover', '-s', 'autotrade_v2/tests', '-p', 'test_*.py', '-v']]
        commands += [[sys.executable, '-c', 'from app.main import app; assert app.title == "AutoTrade V2"; print("FastAPI import PASS")']]
    else:
        commands += [[sys.executable, '-m', 'pytest', 'dashboard/tests', '-q']]
    for command in commands:
        print('RUN', ' '.join(command), flush=True)
        subprocess.run(command, cwd=root, env=env, check=True)
    print(f'PASS: {service}; {len(commands)} commands; external sockets blocked')
