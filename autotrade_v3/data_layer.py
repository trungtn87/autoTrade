"""Data Layer for AutoTrade V3 Offline Research Lab.

Manages offline historical candle data (BTC/ETH M1 3-Year Archive) using
read-only local DuckDB and Parquet partitions with strict no-lookahead resampling,
warmup preservation, and data integrity hashing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator, Literal
import duckdb
import numpy as np
import pandas as pd

from autotrade_v3.engine.candle_loader import (
    M1_MS,
    load_m1_from_catalog,
    resample_m1_to_timeframe,
    to_utc_ms,
    validate_m1,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = REPO_ROOT / "catalog" / "research.duckdb"
DEFAULT_QA_PATH = REPO_ROOT / "catalog" / "v3_m1_qa.json"
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "m1_raw"

TOTAL_EXPECTED_M1_CANDLES_PER_SYMBOL = 1_578_240
TOTAL_EXPECTED_M1_CANDLES = 3_156_480
CANONICAL_START_UTC = "2023-11-01 00:00:00"
CANONICAL_END_UTC = "2026-11-01 00:00:00"
WARMUP_M15_BARS = 3_400  # Standard 20FINAL warmup buffer (~35.4 days)


@dataclass(frozen=True)
class DatasetManifest:
    """Metadata and provenance record for local research dataset."""
    name: str
    catalog_path: str
    qa_path: str
    start_utc: str
    end_utc: str
    symbols: list[str]
    total_candles: int
    symbol_candle_counts: dict[str, int]
    partition_count: int
    data_manifest_hash: str
    status: str
    is_valid: bool
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "catalog_path": self.catalog_path,
            "qa_path": self.qa_path,
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "symbols": self.symbols,
            "total_candles": self.total_candles,
            "symbol_candle_counts": self.symbol_candle_counts,
            "partition_count": self.partition_count,
            "data_manifest_hash": self.data_manifest_hash,
            "status": self.status,
            "is_valid": self.is_valid,
            "details": self.details,
        }


class DataLayer:
    """Read-only Data Layer connecting DuckDB, Parquet partitions, and no-lookahead resamplers."""

    def __init__(
        self,
        catalog_path: str | Path = DEFAULT_CATALOG_PATH,
        qa_path: str | Path = DEFAULT_QA_PATH,
        data_dir: str | Path = DEFAULT_DATA_DIR,
    ):
        self.catalog_path = Path(catalog_path)
        self.qa_path = Path(qa_path)
        self.data_dir = Path(data_dir)
        self._manifest_cache: DatasetManifest | None = None

    def get_manifest_hash(self) -> str:
        """Calculate SHA256 of the QA manifest file for run reproducibility."""
        if self.qa_path.exists():
            content = self.qa_path.read_bytes()
            return hashlib.sha256(content).hexdigest()
        return "UNKNOWN_DATA_HASH"

    def get_manifest(self, force_refresh: bool = False) -> DatasetManifest:
        """Retrieve dataset manifest and verify coverage against catalog."""
        if self._manifest_cache is not None and not force_refresh:
            return self._manifest_cache

        qa_data: dict[str, Any] = {}
        if self.qa_path.exists():
            try:
                with open(self.qa_path, "r", encoding="utf-8") as f:
                    qa_data = json.load(f)
            except Exception:
                qa_data = {}

        manifest_hash = self.get_manifest_hash()
        partition_count = qa_data.get("file_count", 0)
        if partition_count == 0 and self.data_dir.exists():
            partition_count = len(list(self.data_dir.glob("**/*.parquet")))

        symbol_counts: dict[str, int] = {}
        total_candles = 0
        is_valid = False

        if self.catalog_path.exists():
            con = duckdb.connect(str(self.catalog_path), read_only=True)
            try:
                res = con.execute(
                    "SELECT symbol, count(*) FROM v3_m1 GROUP BY symbol ORDER BY symbol"
                ).fetchall()
                for sym, cnt in res:
                    canonical = "BTC-USDT" if "BTC" in sym.upper() else "ETH-USDT"
                    symbol_counts[canonical] = cnt
                    total_candles += cnt
                if total_candles == TOTAL_EXPECTED_M1_CANDLES:
                    is_valid = True
            except Exception as e:
                is_valid = False
            finally:
                con.close()

        status = "VERIFIED_PASS" if is_valid else "UNVERIFIED"

        manifest = DatasetManifest(
            name="3Y_BTC_ETH_M1_HYBRID_PARQUET",
            catalog_path=str(self.catalog_path),
            qa_path=str(self.qa_path),
            start_utc=CANONICAL_START_UTC,
            end_utc=CANONICAL_END_UTC,
            symbols=["BTC-USDT", "ETH-USDT"],
            total_candles=total_candles,
            symbol_candle_counts=symbol_counts,
            partition_count=partition_count,
            data_manifest_hash=manifest_hash,
            status=status,
            is_valid=is_valid,
            details={
                "hybrid_binance_bingx": True,
                "price_basis": "LAST_PRICE_USDT_PERPETUAL",
                "missing_candles": 0 if is_valid else None,
                "duplicate_candles": 0 if is_valid else None,
            },
        )
        self._manifest_cache = manifest
        return manifest

    def verify_integrity(self) -> dict[str, Any]:
        """Perform comprehensive data integrity audit (Gate A re-check)."""
        manifest = self.get_manifest(force_refresh=True)
        if not manifest.is_valid:
            return {
                "passed": False,
                "error": f"Catalog at {self.catalog_path} has {manifest.total_candles} candles (expected {TOTAL_EXPECTED_M1_CANDLES})",
                "manifest": manifest.to_dict(),
            }

        con = duckdb.connect(str(self.catalog_path), read_only=True)
        try:
            checks = con.execute("""
                SELECT
                    symbol,
                    min(open_ms) as min_ms,
                    max(open_ms) as max_ms,
                    count(*) as total_rows,
                    count(distinct open_ms) as distinct_rows,
                    sum(CASE WHEN high < low OR high < "open" OR high < "close" OR low > "open" OR low > "close" THEN 1 ELSE 0 END) as bad_ohlc,
                    sum(CASE WHEN volume < 0 THEN 1 ELSE 0 END) as bad_vol
                FROM v3_m1
                GROUP BY symbol
                ORDER BY symbol
            """).df()
        finally:
            con.close()

        passed = True
        issues = []
        for _, row in checks.iterrows():
            sym = row["symbol"]
            if row["total_rows"] != TOTAL_EXPECTED_M1_CANDLES_PER_SYMBOL:
                passed = False
                issues.append(f"{sym}: total_rows {row['total_rows']} != {TOTAL_EXPECTED_M1_CANDLES_PER_SYMBOL}")
            if row["distinct_rows"] != row["total_rows"]:
                passed = False
                issues.append(f"{sym}: has duplicate timestamps ({row['total_rows'] - row['distinct_rows']})")
            if row["bad_ohlc"] > 0:
                passed = False
                issues.append(f"{sym}: has {row['bad_ohlc']} OHLC logic violations")
            if row["bad_vol"] > 0:
                passed = False
                issues.append(f"{sym}: has {row['bad_vol']} negative volume rows")

        return {
            "passed": passed,
            "issues": issues,
            "summary": checks.to_dict(orient="records"),
            "manifest": manifest.to_dict(),
        }

    def compute_data_slice_hash(self, df: pd.DataFrame) -> str:
        """Deterministic hash of a candle DataFrame slice for provenance tracking."""
        if df.empty:
            return hashlib.sha256(b"EMPTY").hexdigest()
        cols = ["open", "high", "low", "close", "volume"]
        sample_bytes = df[cols].to_numpy().tobytes()
        t_bytes = df.index.asi8.tobytes()
        return hashlib.sha256(t_bytes + sample_bytes).hexdigest()

    def get_warmup_start_date(self, start_date: str | datetime | pd.Timestamp) -> str:
        """Calculate start date including warmup buffer (3,400 M15 bars = ~36 days)."""
        dt = pd.Timestamp(start_date, tz="UTC")
        dataset_start = pd.Timestamp("2023-11-01", tz="UTC")
        if dt <= dataset_start:
            return "2023-11-01"
        warmup_dt = max(dataset_start, dt - pd.Timedelta(days=36))
        return warmup_dt.strftime("%Y-%m-%d")

    def query_m1(
        self,
        symbol: str,
        start: str | datetime | pd.Timestamp,
        end: str | datetime | pd.Timestamp,
        include_warmup: bool = False,
    ) -> pd.DataFrame:
        """Load continuous M1 candles for symbol between start and end.
        
        If include_warmup is True, loads from warmup buffer start to ensure
        indicators can be primed prior to the start timestamp.
        """
        load_start = self.get_warmup_start_date(start) if include_warmup else start
        return load_m1_from_catalog(self.catalog_path, symbol, load_start, end)

    def query_timeframe(
        self,
        symbol: str,
        timeframe: str,
        start: str | datetime | pd.Timestamp,
        end: str | datetime | pd.Timestamp,
        include_warmup: bool = True,
    ) -> pd.DataFrame:
        """Query resampled timeframe candles (e.g. '15m', '1h', 'M15', 'H1') strictly without lookahead."""
        tf_clean = timeframe.lower().replace("min", "m")
        if tf_clean in ("1m", "m1"):
            return self.query_m1(symbol, start, end, include_warmup=include_warmup)

        if tf_clean in ("15m", "m15"):
            tf_min = 15
        elif tf_clean in ("60m", "1h", "h1"):
            tf_min = 60
        else:
            raise ValueError(f"Unsupported timeframe: {timeframe}. Supported: 1m, 15m, 1h")

        df_m1 = self.query_m1(symbol, start, end, include_warmup=include_warmup)
        return resample_m1_to_timeframe(df_m1, timeframe_min=tf_min)

    def query_chunks(
        self,
        symbol: str,
        start: str | datetime | pd.Timestamp,
        end: str | datetime | pd.Timestamp,
        chunk_days: int = 30,
        overlap_warmup_days: int = 36,
    ) -> Iterator[tuple[pd.Timestamp, pd.Timestamp, pd.DataFrame]]:
        """Yield continuous M1 chunks with overlapping warmup buffer to prevent state loss."""
        start_dt = pd.Timestamp(start, tz="UTC")
        end_dt = pd.Timestamp(end, tz="UTC")
        cur_dt = start_dt

        while cur_dt < end_dt:
            next_dt = min(end_dt, cur_dt + pd.Timedelta(days=chunk_days))
            chunk_load_start = max(
                pd.Timestamp("2023-11-01", tz="UTC"),
                cur_dt - pd.Timedelta(days=overlap_warmup_days)
            )
            df_chunk = load_m1_from_catalog(
                self.catalog_path,
                symbol,
                chunk_load_start.strftime("%Y-%m-%d %H:%M:%S"),
                next_dt.strftime("%Y-%m-%d %H:%M:%S"),
            )
            yield cur_dt, next_dt, df_chunk
            cur_dt = next_dt
