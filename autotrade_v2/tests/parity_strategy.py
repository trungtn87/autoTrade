from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from utils import synthetic_15m

ROOT = Path(__file__).resolve().parents[2]


def scan_with(pythonpath: Path, module: str, pickle_path: str) -> dict:
    code = f"""
import json
import pandas as pd
from {module} import scan_latest
df = pd.read_pickle({pickle_path!r})
out = {{}}
for symbol in ("BTC-USDT", "ETH-USDT"):
    out[symbol] = [s.__dict__ for s in scan_latest(symbol, df)]
print(json.dumps(out, sort_keys=True))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(pythonpath)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout.strip())


def main() -> None:
    df = synthetic_15m(3400)
    with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tmp:
        path = tmp.name
    try:
        df.to_pickle(path)
        old = scan_with(ROOT / "signal_engine", "app.final14_exact_strategy", path)
        new = scan_with(ROOT / "autotrade_v2", "app.strategy.final14_exact_strategy", path)
        assert new == old, {"old": old, "new": new}
        print(json.dumps({"ok": True, "strategy_parity": True, "signals": new}, sort_keys=True))
    finally:
        os.unlink(path)


if __name__ == "__main__":
    main()
