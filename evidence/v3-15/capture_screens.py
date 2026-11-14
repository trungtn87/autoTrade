"""Capture illustrative screenshots of the 7 dashboard screens on real RunStore data."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time
import urllib.request

CHROME_PATH = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
SCREENS_DIR = Path("evidence/v3-15/screens")


def main() -> None:
    if not CHROME_PATH.exists():
        print("Chrome not found; skipping screenshots.")
        return

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from autotrade_v3.dashboard import queries

    runs = queries.list_store_runs(Path("runs/lab/store"))
    run_id = str(runs[0]["run_id"]) if runs else "EMPTY"
    SCREENS_DIR.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "autotrade_v3/dashboard/app.py",
            "--server.address",
            "127.0.0.1",
            "--server.port",
            "8598",
            "--server.headless",
            "true",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        for _ in range(30):
            try:
                with urllib.request.urlopen("http://127.0.0.1:8598", timeout=2):
                    break
            except Exception:
                time.sleep(0.5)

        pages = [
            ("01_datasets", "p01_datasets"),
            ("02_strategies", "p02_strategies"),
            ("03_new_run", "p03_new_run"),
            ("04_run_detail", "p04_run_detail"),
            ("05_signal_explorer", "p05_signal_explorer"),
            ("06_compare", "p06_compare"),
            ("07_research_log", "p07_research_log"),
        ]
        for label, slug in pages:
            out_png = SCREENS_DIR / f"{label}_{run_id}.png"
            url = f"http://127.0.0.1:8598/{slug}"
            subprocess.run(
                [
                    str(CHROME_PATH),
                    "--headless=new",
                    "--disable-gpu",
                    "--window-size=1440,960",
                    "--virtual-time-budget=6000",
                    f"--screenshot={out_png.resolve()}",
                    url,
                ],
                check=False,
                capture_output=True,
                timeout=20,
            )
            print(f"Captured {out_png} exists={out_png.exists()}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
