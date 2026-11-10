# Thiết Kế Kiến Trúc & Hướng Dẫn AutoTrade V3 Offline Research Lab Core (V3-07)

**Môi trường:** Windows Local (`D:\AutoTrade`)  
**Branch:** `feat/v3-research-lab-core`  
**Chế độ hoạt động:** `STRICTLY OFFLINE / SIMULATION ONLY`  
**Bộ dữ liệu chuẩn:** 3 Năm BTC/ETH M1 (`2023-11-01` đến `2026-11-01` UTC, 3.156.480 nến)  

---

## 1. Mục Tiêu & Nguyên Tắc Cốt Lõi

AutoTrade V3 Offline Research Lab Core chuyển đổi toàn bộ chuỗi backtest rời rạc thành một nền tảng nghiên cứu lượng lượng tử chuẩn hóa, độc lập và có thể sử dụng lâu dài:

1. **Tam Giác Trách Nhiệm Độc Lập:**
   - **Data Layer:** Quản lý nến M1, kiểm tra toàn vẹn, resample M15/H1 strictly no-lookahead, bảo toàn warm-up. Dữ liệu gốc read-only.
   - **Strategy Engine:** Quyết định tín hiệu (Signals) dựa trên nến đã đóng tại thời điểm quyết định. Không gọi API sàn, không khớp lệnh.
   - **Execution Simulator:** Quyết định khớp lệnh (Entries, Exits, Fees, Slippage, VWAP, Margin, Ambiguity) theo từng chế độ nghiên cứu.
   - **Research Analyzer:** Đo lường hiệu suất đa chiều (Gross/Net PnL, Win Rate Trade vs Leg, Profit Factor, Drawdown, Sub-windows Dev/Val/Holdout).
   - **Run Manager:** Tạo `run_id` duy nhất, lưu trữ bất biến và hỗ trợ so sánh đối chiếu đa chiều (`compare`).

2. **Bảo Mật Offline & An Toàn V2:**
   - Tuyệt đối không gọi BingX API, Supabase, Gemini API hay cloud databases.
   - Không can thiệp, không sửa đổi logic V2 production.

---

## 2. Ba Chế Độ Nghiên Cứu (Research Execution Modes)

| Đặc tính | MODE A: `ALL_SIGNALS` | MODE B: `ONE_TRADE_PER_COMBO` | MODE C: `BINGX_PORTFOLIO` |
| :--- | :--- | :--- | :--- |
| **Mục đích** | Đo lường chất lượng & cạnh tranh thô của thuật toán | Đánh giá chiến lược theo mô hình vị thế đơn lập combo | Đánh giá khả thi vận hành & dòng tiền tài khoản thật |
| **Quy tắc Concurrency** | **Không bỏ qua bất kỳ tín hiệu nào** (0 skips) | Mỗi combo chỉ giữ tối đa 1 lệnh mở. Trùng lệnh -> `SKIPPED_BY_CONCURRENCY` | Mỗi combo giữ tối đa 1 lệnh; gộp vị thế cùng chiều trên symbol theo Hedge Mode |
| **Mô hình Vị thế** | Vị thế ảo độc lập từng tín hiệu | Vị thế ảo độc lập từng combo | Vị thế gộp thực tế trên sàn với giá vốn bình quân (VWAP) |
| **Ràng buộc Ký quỹ (Margin)** | Không áp dụng giới hạn margin | Không áp dụng giới hạn margin | Kiểm tra Free Margin thời gian thực (`INSUFFICIENT_MARGIN`) |
| **Cách tính ROI** | ROI trên vốn tham chiếu danh nghĩa ($1.000) | ROI trên vốn phân bổ tham chiếu ($1.000) | ROI trên tổng vốn tài khoản thật ($1.000 Initial Equity) |
| **Theo dõi Drawdown** | Closed-trade Drawdown | Closed-trade Drawdown | Continuous Mark-to-Market (M2M) Intra-trade Drawdown |

---

## 3. Cấu Trúc Module

```
autotrade_v3/
├── data_layer.py              # Data Layer: DuckDB catalog, partition checking, warmup query
├── strategy/                  # Strategy Engine
│   ├── base.py                # BaseStrategy & ExitPolicy contracts
│   ├── baseline_20final.py    # 20FINAL_BASELINE frozen strategy (20 combos)
│   └── registry.py            # StrategyRegistry
├── engine/
│   ├── candle_loader.py       # M1 validation, resampling no-lookahead
│   ├── execution_simulator.py # Simulator hỗ trợ 3 modes (A, B, C), SL conservative tie-break
│   ├── risk_manager.py        # Phí taker/maker, ký quỹ, trượt giá
│   └── signal_engine.py       # Cầu nối tín hiệu 20FINAL
├── analysis/
│   ├── metrics.py             # Metrics cơ bản
│   ├── regimes.py             # Regime evaluation
│   └── research_analyzer.py   # Phân tích toàn diện PnL, Win Rate (Trade/Leg), Subwindows, Compare
├── lab/
│   ├── run_manager.py         # Quản lý run_id, lưu trữ bất biến và catalog indexing
│   └── experiment.py          # Pipeline thực thi thí nghiệm trọn gói
├── cli.py                     # CLI điều khiển toàn bộ nền tảng
└── tests/
    ├── test_research_lab_12checks.py  # 12 bài kiểm tra nghiệm thu V3-07
    └── test_offline_boundary.py       # Kiểm tra an toàn offline (socket blocked)
```

---

## 4. Hướng Dẫn Sử Dụng CLI (`autotrade_v3.cli`)

### 1. Xem danh mục dataset offline:
```powershell
.\.venv\Scripts\python.exe -m autotrade_v3.cli datasets --verify
```

### 2. Liệt kê các chiến lược đã đăng ký:
```powershell
.\.venv\Scripts\python.exe -m autotrade_v3.cli strategies
```

### 3. Xem danh sách các chế độ thực thi:
```powershell
.\.venv\Scripts\python.exe -m autotrade_v3.cli modes
```

### 4. Chạy backtest:
```powershell
# Chạy cả 3 modes trên dữ liệu 3 năm
.\.venv\Scripts\python.exe -m autotrade_v3.cli backtest --strategy 20FINAL_BASELINE --mode ALL --start 2023-11-01 --end 2026-11-01

# Chạy riêng Mode A (ALL_SIGNALS)
.\.venv\Scripts\python.exe -m autotrade_v3.cli backtest --strategy 20FINAL_BASELINE --mode ALL_SIGNALS

# Chạy riêng Mode C (BINGX_PORTFOLIO) có trượt giá 0.01%
.\.venv\Scripts\python.exe -m autotrade_v3.cli backtest --strategy 20FINAL_BASELINE --mode BINGX_PORTFOLIO --slippage 0.0001
```

### 5. Xem lịch sử các lượt chạy:
```powershell
.\.venv\Scripts\python.exe -m autotrade_v3.cli runs
```

### 6. So sánh các lượt chạy:
```powershell
.\.venv\Scripts\python.exe -m autotrade_v3.cli compare <RUN_ID_1> <RUN_ID_2> <RUN_ID_3>
```

### 7. Xuất và xem báo cáo chi tiết của một run:
```powershell
.\.venv\Scripts\python.exe -m autotrade_v3.cli report <RUN_ID>
```

---

## 5. Sẵn Sàng Cho Research Dashboard V3-08

Kiến trúc V3-07 cung cấp đầy đủ các API nội bộ phục vụ trực tiếp cho việc xây dựng Local Dashboard ở V3-08:
- `DataLayer.get_manifest()`: Cung cấp metadata bộ dữ liệu.
- `StrategyRegistry.list_strategies()`: Trả về danh sách chiến lược dạng JSON.
- `RunManager.list_runs()` / `get_run(run_id)`: Trả về metadata và toàn bộ timeline trade / snapshot phục vụ render biểu đồ equity curve, drawdown và nến.
- `ResearchAnalyzer.compare_runs([run_ids])`: Cung cấp ma trận so sánh đối sánh giữa các thuật toán.
