"""Test import boundary for autotrade_v3.validation package.

Specification Section 3:
 mã trong autotrade_v3/validation/ KHÔNG được import autotrade_v3.engine.execution_simulator,
 autotrade_v3.analysis hay autotrade_v3.lab. Chỉ được import pandas, numpy, duckdb, pyarrow,
 thư viện chuẩn, và lớp truy cập dữ liệu chỉ đọc.
"""
from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_PREFIXES = (
    "autotrade_v3.engine.execution_simulator",
    "autotrade_v3.analysis",
    "autotrade_v3.lab",
    "autotrade_v2",
)


def test_validation_import_boundary():
    val_dir = Path(__file__).resolve().parents[1] / "validation"
    assert val_dir.exists(), f"validation package directory not found: {val_dir}"

    py_files = sorted(val_dir.glob("*.py"))
    assert len(py_files) > 0, "No python files found in validation package"

    violations = []
    for pf in py_files:
        tree = ast.parse(pf.read_text(encoding="utf-8"), filename=str(pf))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name
                    for f_pre in FORBIDDEN_PREFIXES:
                        if mod == f_pre or mod.startswith(f_pre + "."):
                            violations.append((pf.name, node.lineno, f"import {mod}"))
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for f_pre in FORBIDDEN_PREFIXES:
                    if mod == f_pre or mod.startswith(f_pre + "."):
                        violations.append((pf.name, node.lineno, f"from {mod} import ..."))

    assert not violations, f"Forbidden imports detected in validation package: {violations}"
