# GEMINI REPRODUCTION COMMANDS (INDEPENDENT GITHUB AUDIT)

Các lệnh dưới đây cho phép bất kỳ auditor độc lập nào (hoặc Codex/Gemini) clone sạch từ GitHub và tái kiểm chứng toàn bộ code, unit tests, negative tests và 27 cấu hình backtest trên `feat/v3-c14-loo-h2`.

## 1. Clone sạch nhánh nghiên cứu từ GitHub

```bash
git clone https://github.com/trungtn87/autoTrade.git autoTrade_audit
cd autoTrade_audit
git fetch origin
git checkout feat/v3-c14-loo-h2
git status --porcelain   # Phải rỗng (clean tree)
```

*(Lưu ý: cần gắn/link thư mục dữ liệu M1 `data/` và `catalog/` có dataset hash `ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628` trước khi chạy backtest.)*

## 2. Chạy toàn bộ Unit, Negative & Regression Tests (43 tests)

```bash
pytest autotrade_v3/tests/test_filter_aware_validation.py \
       autotrade_v3/tests/test_c14_ichimoku_atr_causality.py \
       autotrade_v3/tests/test_fixed_tpsl_v1.py \
       autotrade_v3/tests/test_fixed_tpsl_execution_scenarios.py \
       autotrade_v3/tests/test_gate_c1_cost_profiles.py -v
```

Kỳ vọng: `43 passed`.

## 3. Kiểm tra Phạm vi Bảo vệ (Protected Paths Diff = 0)

```bash
git diff --stat origin/v3_lab..HEAD -- \
  autotrade_v3/live \
  autotrade_v2 \
  unified_app.py \
  autotrade_v3/strategy_core \
  runs/golden
```

Kỳ vọng: Không có thay đổi nào do nhánh `feat/v3-c14-loo-h2` gây ra ở các đường dẫn live/strategy/golden.

## 4. Tái lập Clean-Tree 27 Runs (`H0`, `LOO-19`, `H2b` × 3 Modes × 3 Profiles)

```bash
python scripts/run_clean_tree_repro.py
```

Kỳ vọng:
- Preflight in `CLEAN-TREE PREFLIGHT OK | code_dirty=False`.
- Tạo 27 run trong `runs/v3_c14_clean_acceptance/` và xuất `RUN_MANIFESTS_AND_HASHES.csv`, `FINAL_GATE_MATRIX.csv`.
- `BINGX_PORTFOLIO` Net PnL:
  - `H0`: `+$516.3583` (`IDEAL`), `+$338.0100` (`BASE`), `+$57.2547` (`STRESS`)
  - `LOO-19`: `+$499.0916` (`IDEAL`), `+$331.9564` (`BASE`), `+$68.6206` (`STRESS`)
  - `H2b`: `+$525.0876` (`IDEAL`), `+$348.2177` (`BASE`), `+$66.7973` (`STRESS`)

## 5. Kiểm tra riêng Chẩn đoán Feature & Độ nhạy Kumo

```bash
python scripts/analyze_c14_features.py
```
