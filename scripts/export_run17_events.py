"""Export RUN17 events for Gate B2 comparison."""
import csv
from pathlib import Path
import pandas as pd
from autotrade_v3.data_backtest.replay_20final import replay_symbol, modules

def main():
    out_dir = Path("D:/AutoTrade/runs/run17_events")
    out_dir.mkdir(parents=True, exist_ok=True)
    m = modules()

    fields = [
        "symbol", "combo_id", "combo", "timeframe", "side", "native_open_ms",
        "signal_close_ms", "bar_position", "smc_dir", "ob_blocked", "approved",
        "warmup_ready", "price_provider_at_close"
    ]

    for sym, path in [("BTC-USDT", "D:/AutoTrade/data/BTCUSDT_RUN17_M15.csv"),
                      ("ETH-USDT", "D:/AutoTrade/data/ETHUSDT_RUN17_M15.csv")]:
        print(f"Replaying {sym}...")
        df = pd.read_csv(path)
        df.index = pd.to_datetime(df["open_ms"], unit="ms", utc=True)
        events, counts = replay_symbol(sym, df[["open", "high", "low", "close", "volume"]], m)
        clean_sym = sym.replace("-", "")
        out_file = out_dir / f"{clean_sym}_run17_events.csv"
        with out_file.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(events)
        print(f"Wrote {out_file} with {len(events)} events.")

if __name__ == "__main__":
    main()
