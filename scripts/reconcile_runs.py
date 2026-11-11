#!/usr/bin/env python3
"""CLI wrapper for legacy cross-run reconciliation.

LEGACY CLASSIFICATION (Design Doc Section 4.6):
Mã lịch sử V3-03 đến V3-06, không dùng để báo cáo V3 Research Lab chính thức.
Báo cáo chính thức của V3 Research Lab được sinh qua lab/run_manager.py và tools/verify_report.py.
Forwarding to autotrade_v3.legacy.reconcile_runs.
"""
from __future__ import annotations

from pathlib import Path
import sys
import warnings

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autotrade_v3.legacy.reconcile_runs import main, run_cross_reconciliation

__all__ = ["main", "run_cross_reconciliation"]

if __name__ == "__main__":
    warnings.warn(
        "scripts/reconcile_runs.py là mã lịch sử V3-03 đến V3-06, không dùng để báo cáo V3 Research Lab chính thức.",
        DeprecationWarning,
        stacklevel=2,
    )
    sys.exit(main())
