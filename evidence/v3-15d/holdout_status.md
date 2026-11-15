# Holdout Status & Evaluation Governance (V3-15d, AT2-002)

- **Date**: 2026-11-15
- **Decision Status**: `20FINAL@1` remains unchanged as the active production baseline. All `P0..P7` and `OPT_1A/1B/OPT_2/OPT_3` variants are **research-only**.

## 1. Internal Holdout Exhausted
- The 2026 window (`2026-01-01` to `2026-11-01`) is **NO LONGER** an independent holdout / OOS set.
- It has been used as a **VALIDATION** set to inspect and select post-hoc research variants (`P6`, `OPT_1A`, `OPT_1B`, `OPT_2`, `OPT_3`) after viewing all 3 years (`2023-11-01` to `2026-11-01`).
- Any metrics reported on `2026` for these variants must be labeled **VALIDATION (post-hoc selection)**, never `HOLDOUT` or `OOS`.

## 2. Only Remaining Independent Evidence
- The only remaining independent out-of-sample evidence is **forward data from `2026-11-14` onward** (live + shadow execution).

## 3. Mandatory Rule Going Forward
- **Pre-registration / Freeze before evaluation**: Any candidate strategy or policy variant MUST have its configuration frozen (explicit config/spec hash committed) **BEFORE** inspecting or running on forward evaluation data.
- **Production baseline**: `20FINAL@1` is unchanged; no research variant is promoted without forward validation under the frozen-config rule.
