# Note on `run_manifest.json` vs `validation.json` Status (`runs/golden/v3-13/`)

- **Date**: 2026-11-15
- **Reference**: Codex Audit Finding (P2) — `runs/golden/v3-13/*/run_manifest.json` contains `"validation_status": "FAILED"` while `runs/golden/v3-13/*/validation.json` has `"overall_status": "VERIFIED"`.
- **Root Cause**: During V3-13 golden generation, `run_manifest.json` was written at simulation completion (prior to running `cli.py verify`), so its initial `validation_status` field remained `"FAILED"` while `validation.json` was subsequently written with `"overall_status": "VERIFIED"` after all 12 gates passed.
- **Policy & Fix Implemented (without mutating golden hashes)**:
  1. Golden files under `runs/golden/v3-13/` are **NOT** modified so all `SHA256SUMS.txt` hashes remain intact.
  2. `autotrade_v3/runstore/index.py` (`_extract_row`) and `autotrade_v3/dashboard/queries.py` (`_effective_validation_status`, `list_runs`) now treat `validation.json` (`overall_status`) as the **single source of truth** for validation status (never falling back to `run_manifest.json["validation_status"]`; if `validation.json` is absent, status defaults to `"UNVERIFIED"`).
  3. Unit tests in `autotrade_v3/tests/test_v315b_audit_fixes.py` verify that when `run_manifest.json` has `"validation_status": "FAILED"` and `validation.json` has `"overall_status": "VERIFIED"`, both `StoreIndex` and dashboard `queries` report `"VERIFIED"`.
- **Owner Action (Optional)**: Updating `run_manifest.json` in `runs/golden/v3-13/` would require regenerating the golden bundle and its `SHA256SUMS.txt`; ask Owner before touching golden artifacts.
