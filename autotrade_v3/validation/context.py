"""Read-only access to run artifacts and M1 data for the validation gates.

Nothing in this module writes to the run directory or the dataset. The only writer in the
package is :mod:`autotrade_v3.validation.runner`, which writes ``validation.json``.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

M1_MS = 60_000

REQUIRED_RUN_FILES = ("run_manifest.json", "ARTIFACTS.json")


def canon_symbol(sym: str) -> str:
    """BTC-USDT -> BTCUSDT (storage key)."""
    return sym.replace("-", "").replace("_", "").upper()


def to_ms(ts: str) -> int:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return int(t.value // 1_000_000)


@dataclass
class M1Series:
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    provider: np.ndarray


class M1Store:
    """Loads M1 candles of every partition listed in a dataset manifest (read-only)."""

    def __init__(self, dataset_root: Path, partitions: list[dict[str, Any]]):
        self.root = Path(dataset_root)
        self.partitions = partitions
        self._cache: dict[str, M1Series] = {}

    @staticmethod
    def partition_symbol(rel_path: str, entry: dict[str, Any] | None = None) -> str:
        if entry and entry.get("symbol"):
            return canon_symbol(str(entry["symbol"]))
        for part in Path(rel_path).parts:
            if part.startswith("symbol="):
                return canon_symbol(part.split("=", 1)[1])
        return "UNKNOWN"

    def partition_paths(self, symbol: str) -> list[Path]:
        key = canon_symbol(symbol)
        out = []
        for p in sorted(self.partitions, key=lambda x: x["relative_path"]):
            if self.partition_symbol(p["relative_path"], p) == key:
                out.append(self.root / p["relative_path"])
        return out

    def symbols(self) -> list[str]:
        return sorted({self.partition_symbol(p["relative_path"], p) for p in self.partitions})

    def get(self, symbol: str) -> M1Series:
        key = canon_symbol(symbol)
        if key in self._cache:
            return self._cache[key]
        frames = []
        for path in self.partition_paths(key):
            tbl = pq.read_table(path, columns=["open_time", "open", "high", "low", "close", "volume", "provider"])
            frames.append(tbl.to_pandas())
        if not frames:
            raise FileNotFoundError(f"no M1 partitions for {symbol}")
        df = pd.concat(frames, ignore_index=True)
        df = df.sort_values("open_time", kind="stable").reset_index(drop=True)
        series = M1Series(
            t=df["open_time"].to_numpy(dtype=np.int64),
            o=df["open"].to_numpy(dtype=float),
            h=df["high"].to_numpy(dtype=float),
            l=df["low"].to_numpy(dtype=float),
            c=df["close"].to_numpy(dtype=float),
            v=df["volume"].to_numpy(dtype=float),
            provider=df["provider"].astype(str).str.strip().str.upper().to_numpy(),
        )
        self._cache[key] = series
        return series


def _read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class RunBundle:
    """Lazy reader of the files in a run directory. Missing/unreadable files are recorded."""

    run_dir: Path
    errors: dict[str, str] = field(default_factory=dict)
    _cache: dict[str, Any] = field(default_factory=dict)

    def _json(self, name: str) -> Any:
        if name in self._cache:
            return self._cache[name]
        p = self.run_dir / name
        val: Any = None
        if not p.exists():
            self.errors[name] = "missing"
        else:
            try:
                val = _read_json(p)
            except Exception as e:  # noqa: BLE001 - recorded, never swallowed silently
                self.errors[name] = f"unreadable: {e}"
        self._cache[name] = val
        return val

    def _parquet(self, name: str) -> pd.DataFrame | None:
        if name in self._cache:
            return self._cache[name]
        p = self.run_dir / name
        val: pd.DataFrame | None = None
        if not p.exists():
            self.errors[name] = "missing"
        else:
            try:
                val = pd.read_parquet(p)
            except Exception as e:  # noqa: BLE001
                self.errors[name] = f"unreadable: {e}"
        self._cache[name] = val
        return val

    @property
    def manifest(self) -> dict[str, Any] | None:
        return self._json("run_manifest.json")

    @property
    def artifacts(self) -> dict[str, Any] | None:
        return self._json("ARTIFACTS.json")

    @property
    def analysis(self) -> dict[str, Any] | None:
        return self._json("analysis.json")

    @property
    def signals(self) -> pd.DataFrame | None:
        return self._parquet("signals.parquet")

    @property
    def trades(self) -> pd.DataFrame | None:
        return self._parquet("trades.parquet")

    @property
    def events(self) -> pd.DataFrame | None:
        return self._parquet("execution_events.parquet")

    @property
    def skipped(self) -> pd.DataFrame | None:
        return self._parquet("skipped_signals.parquet")

    @property
    def rejected(self) -> pd.DataFrame | None:
        return self._parquet("rejected_signals.parquet")

    @property
    def snapshots(self) -> pd.DataFrame | None:
        if not (self.run_dir / "snapshots.parquet").exists():
            return None
        return self._parquet("snapshots.parquet")

    @property
    def report_text(self) -> str | None:
        p = self.run_dir / "report.md"
        if not p.exists():
            self.errors["report.md"] = "missing"
            return None
        return p.read_text(encoding="utf-8")

    def dataset_manifest_path(self) -> Path | None:
        for cand in (self.run_dir / "dataset_manifest.json", self.run_dir.parent / "dataset_manifest.json"):
            if cand.exists():
                return cand
        return None

    @property
    def dataset_manifest(self) -> dict[str, Any] | None:
        if "dataset_manifest" in self._cache:
            return self._cache["dataset_manifest"]
        p = self.dataset_manifest_path()
        val: dict[str, Any] | None = None
        if p is None:
            self.errors["dataset_manifest.json"] = "missing"
        else:
            try:
                val = _read_json(p)
            except Exception as e:  # noqa: BLE001
                self.errors["dataset_manifest.json"] = f"unreadable: {e}"
        self._cache["dataset_manifest"] = val
        return val


def parse_policy_cell(cell: Any) -> dict[str, Any] | None:
    """signals.parquet stores the ExitPolicy as the str() of a dict."""
    if cell is None or (isinstance(cell, float) and np.isnan(cell)):
        return None
    if isinstance(cell, dict):
        return cell
    try:
        val = ast.literal_eval(str(cell))
    except (ValueError, SyntaxError):
        return None
    return val if isinstance(val, dict) else None


@dataclass
class VerifyContext:
    run_dir: Path
    dataset_root: Path
    repo_root: Path
    reference_signals: Path | None = None
    n_cutoffs: int = 20
    seed: int = 20261110
    run: RunBundle = field(init=False)
    _store: M1Store | None = None
    cache: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.run = RunBundle(self.run_dir)

    @property
    def mode(self) -> str:
        m = self.run.manifest or {}
        return str(m.get("execution_mode", ""))

    @property
    def is_synthetic(self) -> bool:
        dm = self.run.dataset_manifest or {}
        m = self.run.manifest or {}
        if str(dm.get("label", "")).upper() == "SYNTHETIC":
            return True
        return str(m.get("dataset_hash", "")).upper().startswith("SYNTHETIC")

    @property
    def data_root(self) -> Path:
        """Directory against which manifest partition paths are resolved."""
        dm = self.run.dataset_manifest or {}
        if str(dm.get("root", "")) == "run_dir":
            return self.run_dir
        return self.dataset_root

    def m1_store(self) -> M1Store | None:
        if self._store is None:
            dm = self.run.dataset_manifest
            if not dm:
                return None
            self._store = M1Store(self.data_root, list(dm.get("partitions", [])))
        return self._store

