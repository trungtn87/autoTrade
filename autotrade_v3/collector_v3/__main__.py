"""Điểm vào CLI cho python -m autotrade_v3.collector_v3."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .runner import DEFAULT_DATA_DIR, build_default_sources, run_collector
from .storage import LockError, verify_warehouse


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autotrade_v3.collector_v3",
        description="Bộ thu dữ liệu BingX & Supabase chỉ-đọc cục bộ (V3-17c).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Chạy thu dữ liệu cho các nguồn.")
    p_run.add_argument("--dry-run", action="store_true", help="Chạy thử không ghi file/state.")
    p_run.add_argument("--since", default=None, help="Mốc bắt đầu (ISO-8601 hoặc timestamp ms).")
    p_run.add_argument(
        "--only",
        action="append",
        default=None,
        help="Chỉ chạy các nguồn chỉ định (phân tách bằng dấu phẩy hoặc lặp cờ).",
    )
    p_run.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Thư mục kho dữ liệu.")
    p_run.add_argument("--max-pages", type=int, default=None, help="Giới hạn số trang mỗi nguồn trong 1 lần chạy.")

    p_verify = sub.add_parser("verify", help="Kiểm tra toàn vẹn SHA-256 của kho dữ liệu.")
    p_verify.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Thư mục kho dữ liệu.")

    sub.add_parser("sources", help="Liệt kê danh sách các nguồn được hỗ trợ.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "sources":
        for src in build_default_sources():
            print(src.name)
        return 0

    if args.command == "verify":
        report = verify_warehouse(args.data_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if args.command == "run":
        try:
            manifest = run_collector(
                data_dir=args.data_dir,
                dry_run=args.dry_run,
                since=args.since,
                only=args.only,
                max_pages_per_run=args.max_pages,
            )
        except (LockError, ValueError) as exc:
            print(f"LỖI: {exc}", file=sys.stderr)
            return 2

        print(manifest.message_vi)
        for name, res in manifest.sources.items():
            print(f"  - {name}: [{res.status}/{res.provenance}] {res.message_vi}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
