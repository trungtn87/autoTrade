#!/usr/bin/env python3
"""Scanner for hardcoded backtest metrics and results in active reporting paths.

Enforces invariant N5 / AC5:
"Không gõ tay số lượng tín hiệu, PnL, ROI, Win Rate, Profit Factor hay chữ PASS
vào mã, JSON hoặc báo cáo. Mọi số phải do lệnh chạy sinh ra và truy được về artifact."

Usage:
  python -m autotrade_v3.tools.scan_hardcoded_metrics [files_or_dirs...]
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]

# Rule 1: Direct variable assignments to literal non-zero financial metric numbers
# (excludes 0 or 0.0 which are standard accumulator/counter initializations)
METRIC_ASSIGN_PATTERN = re.compile(
    r"^\s*(?P<var>total_legs|net_pnl|gross_pnl|total_signals|win_rate|win_rate_pct|"
    r"profit_factor|gross_pf|net_pf|roi_pct|roi|accepted_entries|total_trades|"
    r"intrabar_ambiguous)\s*=\s*(?P<val>[-+]?(?!0(?:\.0+)?\b)\d+(?:\.\d+)?)\b"
)

# Rule 2: Hardcoded trading result strings (e.g. '6,271 trade legs', '5,984 signals')
STRING_METRIC_PATTERN = re.compile(
    r"([\"'].*?\b(?:\d{1,3}(?:,\d{3})+|\d{3,})\s+(?:trade\s+legs?|trades?|signals?|entries)\b.*?[\"'])",
    re.IGNORECASE,
)

# Rule 3: Static Markdown table rows containing fixed financial metrics without templating
MARKDOWN_STATIC_TABLE_PATTERN = re.compile(
    r"^\s*\|.*[-+]?\$?\d+.*\|.*[-+]?\d+(?:\.\d+)?%.*\|"
)

# Legacy marker in module docstring
LEGACY_MARKERS = [
    "LEGACY CLASSIFICATION",
    "Mã lịch sử V3-03 đến V3-06, không dùng để báo cáo",
    "lịch sử V3-03 đến V3-06, không dùng để báo cáo",
]

DEFAULT_ACTIVE_SCAN_PATHS = [
    REPO_ROOT / "autotrade_v3" / "lab" / "run_manager.py",
    REPO_ROOT / "autotrade_v3" / "reports" / "reporter.py",
    REPO_ROOT / "scripts" / "run_v3_backtest.py",
    REPO_ROOT / "scripts" / "reconcile_runs.py",
    REPO_ROOT / "autotrade_v3" / "analysis" / "research_analyzer.py",
]


@dataclass
class HardcodeViolation:
    file_path: Path
    line_number: int
    rule_name: str
    line_content: str
    description: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line_number} [{self.rule_name}] {self.description}: {self.line_content.strip()}"


def is_legacy_file(text: str) -> bool:
    """Check if file has an explicit legacy classification header."""
    header = text[:1500]
    return any(marker in header for marker in LEGACY_MARKERS)


def scan_file(file_path: Path, scan_legacy: bool = False) -> list[HardcodeViolation]:
    """Scan a single Python or Markdown file for hardcoded backtest metrics."""
    if not file_path.exists() or not file_path.is_file():
        return []

    # Skip tests directory and test files (exempt as test fixtures)
    normalized = str(file_path.resolve()).replace("\\", "/")
    if "/tests/" in normalized or file_path.name.startswith("test_"):
        return []

    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception:
        return []

    # Skip clearly classified legacy files unless scan_legacy is explicitly requested
    if not scan_legacy and is_legacy_file(text):
        return []

    violations: list[HardcodeViolation] = []
    lines = text.splitlines()

    for idx, raw_line in enumerate(lines, 1):
        stripped = raw_line.strip()

        # Skip Python comments
        if stripped.startswith("#"):
            continue

        # Rule 1: Metric variable assignment
        m_assign = METRIC_ASSIGN_PATTERN.search(stripped)
        if m_assign:
            var_name = m_assign.group("var")
            val_str = m_assign.group("val")
            violations.append(
                HardcodeViolation(
                    file_path=file_path,
                    line_number=idx,
                    rule_name="HARDCODED_METRIC_ASSIGNMENT",
                    line_content=stripped,
                    description=f"Variable '{var_name}' assigned to hardcoded metric constant {val_str}",
                )
            )

        # Rule 2: Hardcoded trading results inside strings
        m_str = STRING_METRIC_PATTERN.search(stripped)
        if m_str:
            violations.append(
                HardcodeViolation(
                    file_path=file_path,
                    line_number=idx,
                    rule_name="HARDCODED_METRIC_STRING",
                    line_content=stripped,
                    description="String contains literal trade/signal/leg count metric",
                )
            )

        # Rule 3: Static Markdown table with fixed results (must contain currency, percentage, and no template interpolation)
        if stripped.startswith("|") and ("+$" in stripped or "-$" in stripped) and "%" in stripped:
            if not ("{" in stripped and "}" in stripped):
                violations.append(
                    HardcodeViolation(
                        file_path=file_path,
                        line_number=idx,
                        rule_name="STATIC_MARKDOWN_TABLE",
                        line_content=stripped,
                        description="Static Markdown table row contains fixed financial metrics without templating",
                    )
                )

    return violations


def scan_for_hardcoded_metrics(
    paths: list[str | Path] | None = None,
    scan_legacy: bool = False,
) -> list[HardcodeViolation]:
    """Scan given paths (files or directories) for hardcoded trading metrics."""
    target_paths = [Path(p) for p in paths] if paths else DEFAULT_ACTIVE_SCAN_PATHS

    all_violations: list[HardcodeViolation] = []
    for p in target_paths:
        if not p.is_absolute():
            p = (REPO_ROOT / p).resolve()
        if p.is_dir():
            for sub_p in p.rglob("*.py"):
                all_violations.extend(scan_file(sub_p, scan_legacy=scan_legacy))
        elif p.is_file():
            all_violations.extend(scan_file(p, scan_legacy=scan_legacy))

    return all_violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan active AutoTrade V3 reporting code for hardcoded backtest metrics (AC5/Invariant N5)"
    )
    parser.add_argument("paths", nargs="*", help="Files or directories to scan (default: active reporting paths)")
    parser.add_argument("--scan-legacy", action="store_true", help="Also scan legacy classified files")
    parser.add_argument("--log-out", type=Path, default=None, help="Optional file path to output scan log")

    args = parser.parse_args(argv)
    scan_paths = [Path(p) for p in args.paths] if args.paths else None

    violations = scan_for_hardcoded_metrics(paths=scan_paths, scan_legacy=args.scan_legacy)

    log_lines = []
    log_lines.append(f"=== AutoTrade V3 Hardcoded Metric Scanner ===")
    log_lines.append(f"Scanned paths: {scan_paths or [str(p.relative_to(REPO_ROOT)) for p in DEFAULT_ACTIVE_SCAN_PATHS]}")
    log_lines.append(f"Total violations found: {len(violations)}")

    if violations:
        log_lines.append("\n[VIOLATIONS DETECTED]")
        for v in violations:
            log_lines.append(f"  {v}")
        result_code = 1
    else:
        log_lines.append("\n[SCAN CLEAN] No hardcoded backtest metrics found in active reporting paths.")
        result_code = 0

    output_text = "\n".join(log_lines)
    print(output_text)

    if args.log_out:
        args.log_out.parent.mkdir(parents=True, exist_ok=True)
        args.log_out.write_text(output_text + "\n", encoding="utf-8")

    return result_code


if __name__ == "__main__":
    sys.exit(main())
