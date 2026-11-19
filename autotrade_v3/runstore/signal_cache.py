"""Signal cache keyed by (dataset_hash, signal_spec_hash, symbols, window+warmup)."""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.runstore.hashing import canonical_json, df_content_hash, sha256_file, sha256_text

logger = logging.getLogger(__name__)


def compute_signal_cache_key(
    *,
    dataset_hash: str,
    signal_spec_hash: str,
    symbols: list[str] | tuple[str, ...],
    start: str,
    end: str,
    include_warmup: bool = True,
) -> str:
    """Compute deterministic cache key for generated signals."""
    payload = {
        "dataset_hash": str(dataset_hash),
        "signal_spec_hash": str(signal_spec_hash),
        "symbols": sorted(str(s) for s in symbols),
        "start": str(start),
        "end": str(end),
        "include_warmup": bool(include_warmup),
    }
    return "SC_" + sha256_text(canonical_json(payload))[:16]


@dataclass
class CacheReadResult:
    hit: bool
    signals_df: pd.DataFrame | None
    meta: dict[str, Any] | None
    warning: str = ""


class SignalCache:
    """Persistent signal cache under ``runs/lab/cache/signals/<key>/``."""

    def __init__(self, cache_dir: str | Path = "runs/lab/cache/signals") -> None:
        self.cache_dir = Path(cache_dir)

    def entry_dir(self, key: str) -> Path:
        return self.cache_dir / key

    def load(self, key: str) -> CacheReadResult:
        entry = self.entry_dir(key)
        pq_path = entry / "signals.parquet"
        meta_path = entry / "meta.json"
        if not pq_path.exists() or not meta_path.exists():
            return CacheReadResult(hit=False, signals_df=None, meta=None, warning="")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            expected_sha = str(meta.get("file_sha256", ""))
            if expected_sha and sha256_file(pq_path) != expected_sha:
                logger.warning("CACHE_CORRUPT (file_sha256 mismatch) for key=%s", key)
                return CacheReadResult(hit=False, signals_df=None, meta=meta, warning="CACHE_CORRUPT")
            df = pd.read_parquet(pq_path)
            actual_hash = df_content_hash(df)
            expected_hash = str(meta.get("content_hash", ""))
            if not expected_hash or actual_hash != expected_hash:
                logger.warning("CACHE_CORRUPT for key=%s: expected=%s actual=%s", key, expected_hash, actual_hash)
                return CacheReadResult(hit=False, signals_df=None, meta=meta, warning="CACHE_CORRUPT")
            return CacheReadResult(hit=True, signals_df=df, meta=meta, warning="")
        except Exception as exc:  # noqa: BLE001
            logger.warning("CACHE_CORRUPT for key=%s: %s", key, exc)
            return CacheReadResult(hit=False, signals_df=None, meta=None, warning="CACHE_CORRUPT")

    def save(
        self,
        key: str,
        signals_df: pd.DataFrame,
        *,
        code_sha: str,
        compute_time_s: float,
        key_inputs: dict[str, Any],
    ) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        entry = self.entry_dir(key)
        content_hash = df_content_hash(signals_df)
        meta = {
            "cache_key": key,
            "content_hash": content_hash,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "code_sha": str(code_sha),
            "compute_time_s": round(float(compute_time_s), 6),
            "row_count": int(len(signals_df)),
            "key_inputs": key_inputs,
        }
        with tempfile.TemporaryDirectory(dir=self.cache_dir) as tmp:
            tmp_entry = Path(tmp) / key
            tmp_entry.mkdir(parents=True, exist_ok=True)
            pq_tmp = tmp_entry / "signals.parquet"
            signals_df.to_parquet(pq_tmp, index=False)
            meta["file_sha256"] = sha256_file(pq_tmp)
            (tmp_entry / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
            if entry.exists():
                shutil.rmtree(entry)
            shutil.move(str(tmp_entry), str(entry))
        return entry

    def stats(self) -> dict[str, Any]:
        if not self.cache_dir.exists():
            return {"cache_dir": str(self.cache_dir), "entries": 0, "total_bytes": 0, "items": []}
        items: list[dict[str, Any]] = []
        total_bytes = 0
        for sub in sorted(self.cache_dir.iterdir()):
            if not sub.is_dir():
                continue
            pq = sub / "signals.parquet"
            mp = sub / "meta.json"
            sz = (pq.stat().st_size if pq.exists() else 0) + (mp.stat().st_size if mp.exists() else 0)
            total_bytes += sz
            meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
            items.append(
                {
                    "cache_key": sub.name,
                    "size_bytes": sz,
                    "row_count": meta.get("row_count"),
                    "created_at_utc": meta.get("created_at_utc"),
                    "compute_time_s": meta.get("compute_time_s"),
                }
            )
        return {
            "cache_dir": str(self.cache_dir),
            "entries": len(items),
            "total_bytes": total_bytes,
            "items": items,
        }


def rebind_exit_policies(signals: list[Any], strategy: Any) -> list[Any]:
    """Rebind exit_policy fields on cached signals to match the active strategy's exit policy."""
    rebound: list[Any] = []
    for sig in signals:
        policy = strategy.get_exit_policy(sig.symbol, sig.combo_id) if hasattr(strategy, "get_exit_policy") else None
        if policy is None:
            cur_ep = sig.exit_policy if hasattr(sig.exit_policy, "split_legs") else None
            rebound.append(replace(sig, exit_policy=cur_ep))
            continue
        tp_pct = float(getattr(policy, "tp_pct", getattr(policy, "tp1_pct", sig.tp_pct)))
        sl_pct = float(getattr(policy, "sl_pct", sig.sl_pct))
        entry_px = float(sig.entry_price)
        if str(sig.side).upper() in ("BUY", "LONG"):
            tp_px = entry_px * (1.0 + tp_pct)
            sl_px = entry_px * (1.0 - sl_pct)
        else:
            tp_px = entry_px * (1.0 - tp_pct)
            sl_px = entry_px * (1.0 + sl_pct)
        if hasattr(policy, "get_policy_hash"):
            policy_hash = policy.get_policy_hash()
        elif hasattr(policy, "policy_hash"):
            policy_hash = policy.policy_hash()
        else:
            policy_hash = sig.exit_policy_hash
        rebound.append(
            replace(
                sig,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                tp_price=tp_px,
                sl_price=sl_px,
                exit_policy=policy if hasattr(policy, "split_legs") else None,
                exit_policy_hash=policy_hash,
            )
        )
    return rebound
