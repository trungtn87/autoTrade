"""Offline annotation store with strict `as_of_ms <= signal_close_ms` causal guard."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.runstore.hashing import canonical_json, sha256_text


def _duckdb():
    """Lazy DuckDB import: only lab stores need it; live imports of this package must not."""
    import duckdb

    return duckdb


logger = logging.getLogger(__name__)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS annotations (
    source VARCHAR,
    symbol VARCHAR,
    as_of_ms BIGINT,
    payload_json VARCHAR,
    payload_hash VARCHAR,
    as_of_unverified BOOLEAN
)
"""


@dataclass(frozen=True)
class AnnotationRecord:
    source: str
    symbol: str
    as_of_ms: int
    payload: dict[str, Any]
    payload_hash: str
    as_of_unverified: bool


@dataclass(frozen=True)
class AnnotationQueryResult:
    records: list[AnnotationRecord]
    dropped_future_count: int
    has_unverified: bool


class AnnotationStore:
    """Persistent offline annotation store backed by DuckDB.

    Filters MUST only read via :meth:`query_as_of`, which strictly enforces
    ``as_of_ms <= signal_close_ms`` in SQL.
    """

    def __init__(self, db_path: str | Path = "runs/lab/annotations/annotations.duckdb") -> None:
        self.db_path = Path(db_path)

    def _ensure_table(self, con: object) -> None:
        con.execute(_CREATE_SQL)

    def import_file(
        self,
        file_path: str | Path,
        *,
        source: str | None = None,
        as_of_verified: bool = True,
    ) -> dict[str, Any]:
        """Import annotations from a local JSON or CSV file.

        Rejects any row missing ``as_of_ms``. If ``as_of_verified=False``,
        marks imported rows with ``as_of_unverified=True`` (``AS_OF_UNVERIFIED``).
        """
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"Annotation file not found: {p}")

        if p.suffix.lower() == ".json":
            raw = json.loads(p.read_text(encoding="utf-8"))
            rows = raw["annotations"] if isinstance(raw, dict) and "annotations" in raw else raw
            if not isinstance(rows, list):
                raise ValueError("JSON annotation file must be a list of objects or {'annotations': [...]}")
        elif p.suffix.lower() == ".csv":
            df = pd.read_csv(p)
            rows = df.to_dict(orient="records")
        else:
            raise ValueError(f"Unsupported annotation file format: {p.suffix}")

        validated: list[tuple[str, str, int, str, str, bool]] = []
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"Row {idx} is not a dict")
            as_of_raw = row.get("as_of_ms")
            if as_of_raw is None or (isinstance(as_of_raw, float) and pd.isna(as_of_raw)) or str(as_of_raw).strip() == "":
                raise ValueError(f"Annotation row {idx} rejected: missing required as_of_ms")
            try:
                as_of_ms = int(as_of_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Annotation row {idx} rejected: invalid as_of_ms={as_of_raw!r}") from exc
            if as_of_ms <= 0:
                raise ValueError(f"Annotation row {idx} rejected: non-positive as_of_ms={as_of_ms}")

            row_source = str(row.get("source") or source or p.stem)
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                raise ValueError(f"Annotation row {idx} rejected: missing symbol")

            if "payload" in row and isinstance(row["payload"], dict):
                payload = row["payload"]
            elif "payload_json" in row and isinstance(row["payload_json"], str):
                payload = json.loads(row["payload_json"])
            else:
                payload = {
                    k: v
                    for k, v in row.items()
                    if k not in ("source", "symbol", "as_of_ms", "as_of_unverified") and not (isinstance(v, float) and pd.isna(v))
                }
            payload_json = canonical_json(payload)
            payload_hash = sha256_text(payload_json)
            row_unverified = (not bool(as_of_verified)) or bool(row.get("as_of_unverified", False))
            validated.append((row_source, symbol, as_of_ms, payload_json, payload_hash, row_unverified))

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = _duckdb().connect(str(self.db_path))
        try:
            self._ensure_table(con)
            if validated:
                con.executemany(
                    "INSERT INTO annotations VALUES (?, ?, ?, ?, ?, ?)",
                    validated,
                )
        finally:
            con.close()

        return {
            "imported_count": len(validated),
            "as_of_unverified": not bool(as_of_verified) or any(r[5] for r in validated),
            "flag": "AS_OF_UNVERIFIED" if (not bool(as_of_verified) or any(r[5] for r in validated)) else "AS_OF_VERIFIED",
            "db_path": str(self.db_path),
        }

    def query_as_of(
        self,
        symbol: str,
        signal_close_ms: int,
        *,
        source: str | None = None,
    ) -> AnnotationQueryResult:
        """Return ONLY annotations with ``as_of_ms <= signal_close_ms``."""
        if not self.db_path.exists():
            return AnnotationQueryResult(records=[], dropped_future_count=0, has_unverified=False)

        cutoff = int(signal_close_ms)
        con = _duckdb().connect(str(self.db_path), read_only=True)
        try:
            if source is not None:
                valid_rows = con.execute(
                    "SELECT source, symbol, as_of_ms, payload_json, payload_hash, as_of_unverified "
                    "FROM annotations WHERE symbol = ? AND source = ? AND as_of_ms <= ? "
                    "ORDER BY as_of_ms ASC",
                    [str(symbol), str(source), cutoff],
                ).fetchall()
                future_cnt = con.execute(
                    "SELECT COUNT(*) FROM annotations WHERE symbol = ? AND source = ? AND as_of_ms > ?",
                    [str(symbol), str(source), cutoff],
                ).fetchone()[0]
            else:
                valid_rows = con.execute(
                    "SELECT source, symbol, as_of_ms, payload_json, payload_hash, as_of_unverified "
                    "FROM annotations WHERE symbol = ? AND as_of_ms <= ? "
                    "ORDER BY as_of_ms ASC",
                    [str(symbol), cutoff],
                ).fetchall()
                future_cnt = con.execute(
                    "SELECT COUNT(*) FROM annotations WHERE symbol = ? AND as_of_ms > ?",
                    [str(symbol), cutoff],
                ).fetchone()[0]
        finally:
            con.close()

        records = [
            AnnotationRecord(
                source=str(r[0]),
                symbol=str(r[1]),
                as_of_ms=int(r[2]),
                payload=json.loads(r[3]),
                payload_hash=str(r[4]),
                as_of_unverified=bool(r[5]),
            )
            for r in valid_rows
        ]
        if future_cnt:
            logger.info(
                "Annotation guard dropped %d future rows (as_of_ms > %d) for symbol=%s",
                int(future_cnt),
                cutoff,
                symbol,
            )
        return AnnotationQueryResult(
            records=records,
            dropped_future_count=int(future_cnt),
            has_unverified=any(rec.as_of_unverified for rec in records),
        )
