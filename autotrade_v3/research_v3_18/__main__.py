"""Giao diện dòng lệnh (CLI) chạy nghiên cứu và xác minh minh chứng V3-18."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from autotrade_v3.research_v3_18.combo_regime_analyzer import (
    load_offline_market_context,
    run_combo_regime_analysis,
)
from autotrade_v3.research_v3_18.portfolio_experiments import run_portfolio_experiments
from autotrade_v3.research_v3_18.regime_features import DEFAULT_CONFIG_PATH, load_research_config
from autotrade_v3.research_v3_18.report_vi import DEFAULT_EVIDENCE_DIR, write_v3_18_artifacts
from autotrade_v3.research_v3_18.verify import verify_evidence_dir


def _ensure_utf8_stdio() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


def run_pipeline(
    *,
    config_path: Path | None = None,
    evidence_dir: Path | None = None,
) -> int:
    """Chạy toàn bộ pipeline nghiên cứu V3-18 ngoại tuyến và xác minh kết quả."""
    cfg_path = config_path or DEFAULT_CONFIG_PATH
    out_dir = evidence_dir or DEFAULT_EVIDENCE_DIR
    cfg = load_research_config(cfg_path)
    ctx = load_offline_market_context(config=cfg)
    combo_analysis = run_combo_regime_analysis(ctx)
    portfolio_results = run_portfolio_experiments(ctx, combo_analysis)
    written = write_v3_18_artifacts(
        ctx=ctx,
        combo_analysis=combo_analysis,
        portfolio_results=portfolio_results,
        evidence_dir=out_dir,
        config_path=cfg_path,
    )
    verify_res = verify_evidence_dir(
        evidence_dir=out_dir,
        config_path=cfg_path,
        verify_data_hash=True,
        write_result=True,
    )
    summary = {
        "written": written,
        "verify_status": verify_res["status"],
        "total_combos": len(combo_analysis["per_combo"]),
        "total_eval_signals": combo_analysis["total_eval_signals"],
        "total_trials_m": portfolio_results["total_trials_m"],
        "candidate_count": len(portfolio_results["candidate_watchlist"]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if verify_res["status"] == "PASS" else 1


def main(argv: Sequence[str] | None = None) -> int:
    _ensure_utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="python -m autotrade_v3.research_v3_18",
        description="V3-18: Nghiên cứu chế độ thị trường & SMC trên đủ 30 mã (chỉ offline)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Chạy toàn bộ pipeline V3-18 và tạo báo cáo evidence/v3-18")
    p_run.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    p_run.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)

    p_ver = sub.add_parser("verify", help="Xác minh toàn bộ minh chứng V3-18")
    p_ver.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    p_ver.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    p_ver.add_argument("--skip-data-hash", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "run":
        return run_pipeline(config_path=args.config, evidence_dir=args.evidence_dir)
    if args.command == "verify":
        res = verify_evidence_dir(
            evidence_dir=args.evidence_dir,
            config_path=args.config,
            verify_data_hash=not bool(args.skip_data_hash),
            write_result=True,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["status"] == "PASS" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
