from __future__ import annotations

from pathlib import Path

import pandas as pd


INTERVAL_15M_MS = 15 * 60_000
_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "warmup"
_SEED_FILES = {
    "BTC-USDT": "BTCUSDT_15m_20260401_20260920__715e866f.pkl",
    "ETH-USDT": "ETHUSDT_15m_20260401_20260920__f30ee5ba.pkl",
}
_REQUIRED = ["open_time", "open", "high", "low", "close", "volume"]


def load_warmup_seed(symbol: str, limit: int = 12000) -> pd.DataFrame:
    """Load canonical BingX 15m seed rows bundled with production."""
    name = _SEED_FILES.get(symbol.upper())
    if not name:
        return pd.DataFrame(columns=_REQUIRED + ["close_time"])

    path = _DATA_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"warmup seed missing: {path}")

    raw = pd.read_pickle(path)
    d = raw.copy(deep=True)

    if "open_time" not in d.columns:
        if isinstance(d.index, pd.DatetimeIndex):
            d["open_time"] = (d.index.view("int64") // 1_000_000).astype("int64")
        else:
            raise ValueError(f"{symbol} seed has no open_time")

    missing = [c for c in _REQUIRED if c not in d.columns]
    if missing:
        raise ValueError(f"{symbol} seed missing columns: {missing}")

    # Canonical PKLs keep open_time both as a DatetimeIndex name and as a
    # numeric column. Drop the index before label-based sorting to avoid
    # pandas' "both an index level and a column label" ambiguity.
    d = d.reset_index(drop=True)
    d = d[_REQUIRED].copy()
    for col in _REQUIRED:
        d[col] = pd.to_numeric(d[col], errors="raise")

    d["open_time"] = d["open_time"].astype("int64")
    d = (
        d.sort_values("open_time")
        .drop_duplicates("open_time", keep="last")
        .reset_index(drop=True)
    )
    d["close_time"] = d["open_time"] + INTERVAL_15M_MS - 1

    if limit > 0 and len(d) > int(limit):
        d = d.tail(int(limit)).reset_index(drop=True)

    if len(d) > 1:
        diffs = d["open_time"].diff().dropna()
        if not bool((diffs == INTERVAL_15M_MS).all()):
            raise ValueError(f"{symbol} warmup seed contains a 15m gap")

    return d
