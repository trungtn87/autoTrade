# Merge Resolution: origin/main into feat/v3-08-golden-baseline

- **Source Branch / Head**: `fa853e85608ae3a30cc2c1f2cb789a10d4a6700d` (origin/feat/v3-research-lab-core, PR #97)
- **Merged Branch**: `940782a8a803be55f745b4338e8e4c420a07be3c` (origin/main)
- **Common Base**: `5d3911332ff1960362144f2375d2dcf1c19734bd`

## Conflict Analysis & Resolution

Both conflicting files had `add/add` conflicts due to independent updates on `main` and `feat/v3-research-lab-core`.
Per instruction B1, the versions from `origin/main` are accepted.

### 1. `.github/workflows/v3-02-replay.yml`
- **Ours (`fa853e8`)**:
  ```yaml
  - run: python -m unittest autotrade_v3.tests.test_replay_20final -v
  ```
- **Theirs (`origin/main`)**:
  ```yaml
  - run: python -m unittest autotrade_v3.tests.test_replay_20final autotrade_v3.tests.test_compare_sources -v
  ```
- **Resolution**: Selected version from `origin/main` which includes `test_compare_sources`.

### 2. `docs/autotrade-v3/02-exact-signal-replay.md`
- **Ours (`fa853e8`)**: Documented pending B1 replay path requirement.
- **Theirs (`origin/main`)**: Updated with completed 20/20 combo-signature parity pass results and reference to `02-b1-b2-verified.md`.
- **Resolution**: Selected version from `origin/main`.

## Verification of Lab Code Preservation
Command:
```bash
git diff fa853e8 HEAD -- autotrade_v3/engine autotrade_v3/strategy autotrade_v3/lab autotrade_v3/analysis autotrade_v3/data_layer.py autotrade_v3/data_backtest/replay_20final.py autotrade_v2
```
Output: EMPTY (0 lines changed).
All research lab code and V2 freeze invariants are 100% preserved.
