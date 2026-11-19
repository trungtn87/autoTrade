"""Immutable content-addressed RunStore (`runs/lab/store/<run_id>/`)."""
from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.runstore.equity import build_equity_df
from autotrade_v3.runstore.hashing import (
    artifact_hashes,
    canonical_json,
    compute_run_id,
    df_content_hash,
    sha256_text,
)
from autotrade_v3.runstore.index import StoreIndex

STORE_ARTIFACTS = (
    "run_manifest.json",
    "signals.parquet",
    "signal_decisions.parquet",
    "legs.parquet",
    "events.parquet",
    "equity.parquet",
    "analysis.json",
    "validation.json",
)


class NonReproducibleError(RuntimeError):
    """Raised when re-running an existing run_id produces different artifact content hashes."""


@dataclass
class StoreSaveResult:
    run_id: str
    run_dir: Path
    in_scratch: bool
    reproducible_hit: bool
    artifacts_index: dict[str, Any]


class RunStore:
    """Manages immutable run directories in ``runs/lab/store`` and dirty runs in ``runs/lab/scratch``."""

    def __init__(
        self,
        store_dir: str | Path = "runs/lab/store",
        scratch_dir: str | Path = "runs/lab/scratch",
    ) -> None:
        self.store_dir = Path(store_dir)
        self.scratch_dir = Path(scratch_dir)
        self.index = StoreIndex(self.store_dir)

    @staticmethod
    def build_run_id(
        *,
        dataset_hash: str,
        spec_hash: str,
        mode: str,
        time_window: dict[str, str],
        symbols: list[str] | tuple[str, ...],
        sim_config_hash: str,
        cost_profile_hash: str,
        ambiguity_policy: str,
        filters: list[dict[str, str]] | None,
        code_sha: str,
    ) -> str:
        return compute_run_id(
            dataset_hash=dataset_hash,
            spec_hash=spec_hash,
            mode=mode,
            time_window=time_window,
            symbols=symbols,
            sim_config_hash=sim_config_hash,
            cost_profile_hash=cost_profile_hash,
            ambiguity_policy=ambiguity_policy,
            filters=filters,
            code_sha=code_sha,
        )

    def resolve_run_dir(self, run_id_or_path: str | Path) -> Path:
        p = Path(run_id_or_path)
        if p.exists() and p.is_dir():
            return p
        in_store = self.store_dir / str(run_id_or_path)
        if in_store.exists():
            return in_store
        in_scratch = self.scratch_dir / str(run_id_or_path)
        if in_scratch.exists():
            return in_scratch
        raise FileNotFoundError(f"Run not found in store or scratch: {run_id_or_path}")

    def _compute_content_hashes(
        self,
        *,
        signals_df: pd.DataFrame,
        decisions_df: pd.DataFrame,
        legs_df: pd.DataFrame,
        events_df: pd.DataFrame,
        equity_df: pd.DataFrame,
        analysis: dict[str, Any],
    ) -> dict[str, str]:
        analysis_clean = {k: v for k, v in analysis.items() if k != "validation"}
        return {
            "signals.parquet": df_content_hash(signals_df),
            "signal_decisions.parquet": df_content_hash(decisions_df),
            "legs.parquet": df_content_hash(legs_df),
            "events.parquet": df_content_hash(events_df),
            "equity.parquet": df_content_hash(equity_df),
            "analysis.json": sha256_text(canonical_json(analysis_clean)),
        }

    def save_run(
        self,
        *,
        run_id: str,
        manifest: dict[str, Any],
        signals_df: pd.DataFrame,
        decisions_df: pd.DataFrame,
        legs_df: pd.DataFrame,
        events_df: pd.DataFrame,
        equity_df: pd.DataFrame | None = None,
        snapshots_df: pd.DataFrame | None = None,
        analysis: dict[str, Any],
        validation: dict[str, Any] | None = None,
        report_md: str = "",
        code_dirty: bool = False,
    ) -> StoreSaveResult:
        """Persist a run atomically to ``store/<run_id>`` or ``scratch/<run_id>``.

        If ``store/<run_id>`` already exists:
        - Compare deterministic content hashes of core tables + analysis.
        - If identical: do not overwrite; record reproducible hit and return.
        - If different: raise ``NonReproducibleError`` without touching existing files.
        """
        mode = str(manifest.get("execution_mode", ""))
        initial_equity = float(manifest.get("initial_equity", 1000.0))
        if equity_df is None:
            equity_df = build_equity_df(
                mode=mode,
                initial_equity=initial_equity,
                legs_df=legs_df,
                snapshots_df=snapshots_df,
            )

        base_root = self.scratch_dir if code_dirty else self.store_dir
        base_root.mkdir(parents=True, exist_ok=True)
        target_dir = base_root / run_id

        new_hashes = self._compute_content_hashes(
            signals_df=signals_df,
            decisions_df=decisions_df,
            legs_df=legs_df,
            events_df=events_df,
            equity_df=equity_df,
            analysis=analysis,
        )

        if not code_dirty and target_dir.exists():
            existing_art_p = target_dir / "ARTIFACTS.json"
            if existing_art_p.exists():
                existing_art = json.loads(existing_art_p.read_text(encoding="utf-8")).get("artifacts", {})
            else:
                existing_art = {}
            diffs: list[str] = []
            for fname, new_h in new_hashes.items():
                if fname == "analysis.json":
                    old_an_p = target_dir / "analysis.json"
                    if old_an_p.exists():
                        old_an = json.loads(old_an_p.read_text(encoding="utf-8"))
                        old_clean = {k: v for k, v in old_an.items() if k != "validation"}
                        old_h = sha256_text(canonical_json(old_clean))
                    else:
                        old_h = ""
                else:
                    old_h = str((existing_art.get(fname) or {}).get("content_hash", ""))
                    if not old_h and (target_dir / fname).exists():
                        old_h = str(artifact_hashes(target_dir / fname)["content_hash"])
                if old_h != new_h:
                    diffs.append(f"{fname}: old={old_h[:12]} != new={new_h[:12]}")
            if diffs:
                raise NonReproducibleError(
                    f"NON_REPRODUCIBLE: run_id={run_id} already exists with different content: "
                    + "; ".join(diffs)
                )
            self.index.upsert_run(target_dir)
            return StoreSaveResult(
                run_id=run_id,
                run_dir=target_dir,
                in_scratch=False,
                reproducible_hit=True,
                artifacts_index=json.loads(existing_art_p.read_text(encoding="utf-8")) if existing_art_p.exists() else {},
            )

        # Write atomically via sibling temp directory
        with tempfile.TemporaryDirectory(dir=base_root) as tmp:
            tmp_dir = Path(tmp) / run_id
            tmp_dir.mkdir(parents=True, exist_ok=True)

            manifest_out = dict(manifest)
            manifest_out["run_id"] = run_id
            manifest_out.setdefault("created_at_utc", datetime.now(timezone.utc).isoformat())
            manifest_out["in_scratch"] = bool(code_dirty)

            (tmp_dir / "run_manifest.json").write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")
            signals_df.to_parquet(tmp_dir / "signals.parquet", index=False)
            decisions_df.to_parquet(tmp_dir / "signal_decisions.parquet", index=False)
            legs_df.to_parquet(tmp_dir / "legs.parquet", index=False)
            events_df.to_parquet(tmp_dir / "events.parquet", index=False)
            equity_df.to_parquet(tmp_dir / "equity.parquet", index=False)
            (tmp_dir / "analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
            (tmp_dir / "validation.json").write_text(
                json.dumps(validation if validation is not None else {}, indent=2),
                encoding="utf-8",
            )
            if report_md:
                (tmp_dir / "report.md").write_text(report_md, encoding="utf-8")

            artifacts_meta: dict[str, Any] = {
                "run_id": run_id,
                "mode": mode,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "artifacts": {},
            }
            for fname in STORE_ARTIFACTS:
                fp = tmp_dir / fname
                if fp.exists():
                    artifacts_meta["artifacts"][fname] = artifact_hashes(fp)
            (tmp_dir / "ARTIFACTS.json").write_text(json.dumps(artifacts_meta, indent=2), encoding="utf-8")

            if target_dir.exists() and code_dirty:
                shutil.rmtree(target_dir)
            shutil.move(str(tmp_dir), str(target_dir))

        if not code_dirty:
            self.index.upsert_run(target_dir)

        return StoreSaveResult(
            run_id=run_id,
            run_dir=target_dir,
            in_scratch=bool(code_dirty),
            reproducible_hit=False,
            artifacts_index=artifacts_meta,
        )

    def update_validation(self, run_dir: Path, validation: dict[str, Any], analysis: dict[str, Any] | None = None, manifest: dict[str, Any] | None = None) -> None:
        """Update validation.json (and ARTIFACTS.json hash) after verification."""
        (run_dir / "validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
        if analysis is not None:
            (run_dir / "analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
        if manifest is not None:
            (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        art_p = run_dir / "ARTIFACTS.json"
        artifacts_meta = json.loads(art_p.read_text(encoding="utf-8")) if art_p.exists() else {"artifacts": {}}
        for fname in ("run_manifest.json", "analysis.json", "validation.json"):
            fp = run_dir / fname
            if fp.exists():
                artifacts_meta["artifacts"][fname] = artifact_hashes(fp)
        art_p.write_text(json.dumps(artifacts_meta, indent=2), encoding="utf-8")
        if run_dir.parent == self.store_dir:
            self.index.upsert_run(run_dir)
