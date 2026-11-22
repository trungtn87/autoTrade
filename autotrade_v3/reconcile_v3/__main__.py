"""CLI entrypoint cho autotrade_v3.reconcile_v3 (hỗ trợ lệnh 'refresh')."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

from autotrade_v3.reconcile_v3.loader import ManifestVerificationError, load_local_store
from scripts.run_v3_17a_reconcile import run_reconcile_pipeline

_AUTH_FAIL_KEYWORDS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "password authentication failed",
    "xác thực",
    "auth",
    "invalid api key",
)


def cmd_refresh(
    *,
    data_root: Path,
    output_dir: Path,
    repo_root: Path,
    skip_collector: bool = False,
) -> int:
    """Chạy collector (dừng ngay nếu lỗi xác thực), verify kho, chạy đối soát 17a và in tóm tắt 1 dòng."""
    if not skip_collector:
        proc = subprocess.run(
            [sys.executable, "-m", "autotrade_v3.collector_v3", "run"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        combined = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
        if proc.returncode != 0:
            is_auth = any(k in combined for k in _AUTH_FAIL_KEYWORDS)
            kind = "LỖI XÁC THỰC COLLECTOR (dừng ngay, không thử lại)" if is_auth else f"LỖI COLLECTOR (exit={proc.returncode})"
            print(f"{kind}: {(proc.stderr or proc.stdout or '').strip()[:400]}", file=sys.stderr)
            return proc.returncode or 1

    try:
        load_local_store(data_root)
    except ManifestVerificationError as exc:
        print(f"LỖI XÁC MINH KHO DỮ LIỆU: {exc}", file=sys.stderr)
        return 2

    payload, _ = run_reconcile_pipeline(
        data_root=data_root,
        output_dir=output_dir,
        repo_root=repo_root,
    )
    sg = payload["sample_governance"]
    sr = payload["signal_reconciliation"]
    n_orders = int(sg.get("closed_orders") or 0)
    dash_status = sg.get("empty_state_label_vi") or ("CHƯA ĐỦ MẪU" if sg.get("is_small_sample") else "ĐỦ MẪU")
    print(
        f"LÀM MỚI 17a HOÀN TẤT | compare_from={payload['compare_from_utc']} | "
        f"lệnh_từ_mốc={n_orders} | "
        f"tín_hiệu(khớp/thiếu/thừa/new6_vp)={sr['matched_count']}/{sr['missing_count']}/{sr['extra_count']}/{sr['new6_virtual_position_count']} | "
        f"dashboard={dash_status} | sha256={payload['report_sha256'][:16]}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m autotrade_v3.reconcile_v3")
    sub = parser.add_subparsers(dest="command", required=True)
    p_ref = sub.add_parser("refresh", help="Làm mới kho, verify, chạy đối soát 17a và in tóm tắt.")
    p_ref.add_argument("--data-root", type=Path, default=Path("D:/AutoTrade/data/bingx"))
    p_ref.add_argument("--output-dir", type=Path, default=Path("D:/AutoTrade/evidence/v3-17a"))
    p_ref.add_argument("--repo-root", type=Path, default=Path("D:/AutoTrade"))
    p_ref.add_argument("--skip-collector", action="store_true", help="Bỏ qua bước gọi collector (chỉ dùng kho local hiện có).")

    args = parser.parse_args(argv)
    if args.command == "refresh":
        return cmd_refresh(
            data_root=args.data_root,
            output_dir=args.output_dir,
            repo_root=args.repo_root,
            skip_collector=bool(args.skip_collector),
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
