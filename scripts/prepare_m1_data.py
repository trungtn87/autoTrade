"""Prepare 3-year M1 dataset from D:/AutoTrade/data zips and generate manifest/catalog."""
import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    base_data = Path("D:/AutoTrade/data")
    raw_dir = base_data / "m1_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # 1. Extract 72 parquet files from the 4 zip files
    zips = [
        "AutoTrade_V3_BTCUSDT_part1.zip",
        "AutoTrade_V3_BTCUSDT_part2.zip",
        "AutoTrade_V3_ETHUSDT_part1.zip",
        "AutoTrade_V3_ETHUSDT_part2.zip"
    ]

    extracted = 0
    for zname in zips:
        zpath = base_data / zname
        print(f"Reading {zname}...")
        with zipfile.ZipFile(zpath) as z:
            for item in z.namelist():
                if item.endswith(".parquet"):
                    dest = raw_dir / item
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if not dest.exists():
                        dest.write_bytes(z.read(item))
                    extracted += 1

    print(f"Total extracted/verified monthly parquets: {extracted}")

    # 2. Check / download 2026-10-28 supplements from BingX (1440 candles each)
    supplements = {
        "BTCUSDT": {
            "sym_bingx": "BTC-USDT",
            "file_rel": "provider=bingx/symbol=BTCUSDT/month=2026-10/BTCUSDT-2026-10-28-BINGX.parquet",
        },
        "ETHUSDT": {
            "sym_bingx": "ETH-USDT",
            "file_rel": "provider=bingx/symbol=ETHUSDT/month=2026-10/ETHUSDT-2026-10-28-BINGX.parquet",
        }
    }

    for sym, info in supplements.items():
        dest = raw_dir / info["file_rel"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            bingx_sym = info["sym_bingx"]
            url = f"https://open-api.bingx.com/openApi/swap/v3/quote/klines?symbol={bingx_sym}&interval=1m&startTime=1793145600000&endTime=1793231999999&limit=1440"
            print(f"Fetching 2026-10-28 supplement for {sym} from BingX...")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            res = json.loads(urllib.request.urlopen(req, timeout=20).read())
            candles = res.get("data", [])
            candles.sort(key=lambda x: x["time"])
            assert len(candles) == 1440, f"Expected 1440 candles, got {len(candles)}"

            rows = []
            for c in candles:
                t = int(c["time"])
                o = float(c["open"])
                h = float(c["high"])
                l = float(c["low"])
                cl = float(c["close"])
                v = float(c["volume"])
                rows.append({
                    "open_time": t,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": cl,
                    "volume": v,
                    "close_time": t + 59999,
                    "quote_volume": v * cl,
                    "trades": 0,
                    "taker_base": 0.0,
                    "taker_quote": 0.0,
                    "ignore": 0,
                    "provider": "bingx",
                    "market": "USD-M perpetual",
                    "price_basis": "last",
                    "symbol": sym,
                    "timestamp_utc": pd.to_datetime(t, unit="ms", utc=True)
                })
            df = pd.DataFrame(rows)
            df.to_parquet(dest, compression="zstd", index=False)
            print(f"Saved {dest} ({len(df)} rows)")
        else:
            print(f"Supplement {dest} already exists.")

    all_pqs = sorted(raw_dir.rglob("*.parquet"))
    print(f"Total Parquet files in m1_raw: {len(all_pqs)}")

    # 3. Generate source_map.json and checksums.json
    source_map_files = []
    checksums = {}

    for pq_path in all_pqs:
        rel_path = str(pq_path.relative_to(raw_dir)).replace("\\", "/")
        h = sha256_file(pq_path)
        checksums[rel_path] = h
        provider = "BINGX" if "provider=bingx" in rel_path else "BINANCE"
        source_map_files.append({
            "path": rel_path,
            "provider": provider,
            "market_type": "USDT_PERP",
            "price_type": "LAST"
        })

    sm_path = base_data / "source-map.json"
    cs_path = base_data / "checksums.json"

    sm_path.write_text(json.dumps({"files": source_map_files}, indent=2), encoding="utf-8")
    cs_path.write_text(json.dumps(checksums, indent=2), encoding="utf-8")

    print(f"Wrote {sm_path} ({len(source_map_files)} files)")
    print(f"Wrote {cs_path} ({len(checksums)} checksums)")

if __name__ == "__main__":
    main()
