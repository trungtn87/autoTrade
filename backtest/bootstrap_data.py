from __future__ import annotations
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from bingx_data import BingXClient, download_history, INTERVAL_MS

def gap_report(df: pd.DataFrame, interval: str) -> dict:
    if df.empty:
        return {"rows": 0, "first": None, "last": None, "duplicates": 0, "missing_intervals": None}
    times = pd.to_numeric(df["open_time"], errors="coerce").dropna().astype("int64").sort_values()
    step = INTERVAL_MS[interval]
    diffs = times.diff().dropna()
    missing = int(((diffs / step) - 1).clip(lower=0).round().sum()) if len(diffs) else 0
    duplicates = int(times.duplicated().sum())
    return {"rows": int(len(df)), "first": str(df.index.min()), "last": str(df.index.max()), "duplicates": duplicates, "missing_intervals": missing}

def main():
    ap = argparse.ArgumentParser(description="Download/cache BingX perpetual OHLCV for research")
    ap.add_argument("--symbols", default="BTC-USDT,ETH-USDT")
    ap.add_argument("--intervals", default="15m,1h,4h,6h")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--sleep", type=float, default=0.15)
    args = ap.parse_args()
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    cli = BingXClient()
    manifest = {"source":"BingX perpetual futures","endpoint":"/openApi/swap/v3/quote/klines","downloaded_at_utc":datetime.now(timezone.utc).isoformat(),"requested_start":args.start,"requested_end":args.end,"datasets":{}}
    for symbol in [x.strip() for x in args.symbols.split(",") if x.strip()]:
        manifest["datasets"][symbol] = {}
        slug = symbol.replace("-", "")
        for interval in [x.strip() for x in args.intervals.split(",") if x.strip()]:
            out = out_dir / f"{slug}_{interval}.pkl"
            print(f"Downloading {symbol} {interval} {args.start} -> {args.end} ...", flush=True)
            df = download_history(cli, symbol, interval, args.start, args.end, out, sleep_s=args.sleep)
            report = gap_report(df, interval); report["path"] = str(out)
            manifest["datasets"][symbol][interval] = report
            print(json.dumps({"symbol": symbol, "interval": interval, **report}), flush=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Manifest: {manifest_path}")

if __name__ == "__main__":
    main()
