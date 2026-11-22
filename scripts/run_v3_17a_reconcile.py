"""CLI chạy đối soát Backtest vs Live (V3-17a) từ kho dữ liệu local."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from autotrade_v3.reconcile_v3.eras import infer_vps_off_windows, list_live_commits
from autotrade_v3.reconcile_v3.keys import verify_live_signals_against_combos
from autotrade_v3.reconcile_v3.loader import load_local_store
from autotrade_v3.reconcile_v3.mirror_sim import run_live_mirror_simulation
from autotrade_v3.reconcile_v3.reconcile_orders import load_tolerances, reconcile_orders
from autotrade_v3.reconcile_v3.report_vi import build_report_payload, render_html_report
from autotrade_v3.reconcile_v3.signals_reconcile import reconcile_signals
from autotrade_v3.reconcile_v3.verify import verify_report


def run_reconcile_pipeline(
    *,
    data_root: Path,
    output_dir: Path,
    repo_root: Path | None = None,
    compare_from_override: str | None = None,
) -> tuple[dict, str]:
    """Chạy toàn bộ pipeline đối soát V3-17a và ghi artifact ra output_dir."""
    tolerances = load_tolerances()
    if compare_from_override:
        tolerances = dict(tolerances)
        tolerances["compare_from"] = compare_from_override
    compare_from_utc = str(tolerances.get("compare_from", "2026-11-22T02:30:33Z"))

    store = load_local_store(data_root)
    verify_live_signals_against_combos(store.strategy_signals)

    vps_off_windows, other_log_gaps = infer_vps_off_windows(
        store.control_logs,
        store.candles_15m,
        min_gap_seconds=int(tolerances.get("vps_off_min_gap_seconds", 7200)),
    )
    live_commits = list_live_commits(repo_root=repo_root)

    signal_summary = reconcile_signals(
        candles_15m=store.candles_15m,
        live_signals=store.strategy_signals,
        execution_orders=store.execution_orders,
        execution_results=store.execution_results,
        vps_off_windows=vps_off_windows,
        compare_from_utc=compare_from_utc,
        deploy_23_finalized_utc=tolerances.get("deploy_23_finalized_utc"),
    )

    mirror_results = run_live_mirror_simulation(
        live_signals=store.strategy_signals,
        execution_orders=store.execution_orders,
        candles_1m=store.candles_1m,
        notional_per_trade=100.0,
        leverage=50.0,
    )

    order_rows = reconcile_orders(
        live_signals=store.strategy_signals,
        execution_orders=store.execution_orders,
        execution_results=store.execution_results,
        execution_finances=store.execution_finances,
        bingx_orders=store.bingx_orders,
        bingx_fills=store.bingx_fills,
        funding_rates=store.funding_rates,
        signal_summary=signal_summary,
        mirror_base=mirror_results["BASE"],
        mirror_ideal=mirror_results["IDEAL"],
        tolerances=tolerances,
    )

    payload = build_report_payload(
        store=store,
        signal_summary=signal_summary,
        order_rows=order_rows,
        vps_off_windows=vps_off_windows,
        other_log_gaps=other_log_gaps,
        live_commits=live_commits,
        tolerances=tolerances,
    )
    html_content = render_html_report(payload)

    vr = verify_report(payload, html_content)
    if not vr.passed:
        raise RuntimeError(f"Xác minh báo cáo thất bại: {vr.errors}")

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "reconcile_report.json"
    html_path = output_dir / "reconcile_report.html"
    verify_path = output_dir / "verify_result.json"

    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    html_path.write_text(html_content, encoding="utf-8")
    verify_path.write_text(
        json.dumps({"passed": vr.passed, "errors": vr.errors, "report_sha256": vr.report_sha256}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return payload, html_content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chạy đối soát Backtest vs Live (V3-17a) từ kho local.")
    parser.add_argument("--data-root", type=Path, default=Path("D:/AutoTrade/data/bingx"))
    parser.add_argument("--output-dir", type=Path, default=Path("D:/AutoTrade/evidence/v3-17a"))
    parser.add_argument("--repo-root", type=Path, default=Path("D:/AutoTrade"))
    args = parser.parse_args(argv)

    payload, _ = run_reconcile_pipeline(
        data_root=args.data_root,
        output_dir=args.output_dir,
        repo_root=args.repo_root,
    )
    print("V3-17a reconcile DONE. report_sha256=", payload["report_sha256"])
    print("Sample governance:", payload["sample_governance"]["warning_vi"])
    for era, es in payload["era_summaries"].items():
        print(f"  {era}: orders={es['orders_count']}, live_net={es['live_net_pnl_usd']}, sim_base_net={es['sim_base_net_pnl_usd']}, sim_ideal_net={es['sim_ideal_net_pnl_usd']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
