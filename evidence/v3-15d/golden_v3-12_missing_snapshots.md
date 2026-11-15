# Golden V3-12 Missing Snapshots Report (V3-15d, AT2-005)

- **Date**: 2026-11-15
- **Constraint**: `runs/golden/v3-12/SHA256SUMS.txt` was NOT modified. This document presents the factual inventory and two options for Owner decision.

## 1. Missing Entries in `runs/golden/v3-12/SHA256SUMS.txt`
On a clean git checkout (e.g. CI), `.gitignore` lines 26–27 ignore `runs/golden/**/snapshots.parquet` and `runs/golden/**/snapshots.csv`, causing `sha256sum --check SHA256SUMS.txt` in `runs/golden/v3-12/` to fail on 2 entries:
1. `BINGX_PORTFOLIO/snapshots.csv` (`sha256`: `61840fe56609e5a49abdf68e4bab6483f3d61b94811043af4de4048e61fb1ec6`, line 30)
2. `BINGX_PORTFOLIO/snapshots.parquet` (`sha256`: `a02644b48fe0cec76bd00a58afb8acb3e40c18b81832c66197dc34ea8c7dca6a`, line 31)

## 2. Existing Ignored Snapshot Files on Local Disk (6 files)
All 6 snapshot files exist locally under `runs/golden/` (untracked due to `.gitignore`):
1. `runs/golden/v3-08/BINGX_PORTFOLIO/snapshots.csv` (81,054,899 bytes)
2. `runs/golden/v3-08/BINGX_PORTFOLIO/snapshots.parquet` (23,619,083 bytes)
3. `runs/golden/v3-12/BINGX_PORTFOLIO/snapshots.csv` (81,054,899 bytes)
4. `runs/golden/v3-12/BINGX_PORTFOLIO/snapshots.parquet` (23,619,083 bytes)
5. `runs/golden/v3-13/BINGX_PORTFOLIO/snapshots.csv` (81,054,899 bytes)
6. `runs/golden/v3-13/BINGX_PORTFOLIO/snapshots.parquet` (23,619,083 bytes)

*(Note: `v3-08/SHA256SUMS.txt` and `v3-13/SHA256SUMS.txt` do not list `snapshots.*`; only `v3-12/SHA256SUMS.txt` includes them.)*

## 3. Options for Owner Decision (Not Auto-Selected)
- **Option A — External / Content-Addressed Storage (Git LFS or artifact locator)**:
  Keep the 2 snapshot hashes in `runs/golden/v3-12/SHA256SUMS.txt` and store the snapshot binaries in Git LFS or a content-addressed object store / locator manifest so CI can fetch and verify them before running `sha256sum --check`.
- **Option B — Regenerate `v3-12` Golden Manifest**:
  Regenerate `runs/golden/v3-12/SHA256SUMS.txt` with `snapshots.csv` and `snapshots.parquet` excluded (matching `v3-08` and `v3-13`), or regenerate the entire `v3-12` golden bundle at a clean commit under explicit Owner sign-off.
