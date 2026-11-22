"""Gắn nhãn chế độ thị trường & cấu trúc SMC hoàn toàn nhân quả (không nhìn tương lai) cho V3-18."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.research_v3_18 import DEFAULT_CONFIG_PATH
from autotrade_v3.strategy_core.indicators.indicators import atr, ema, sma
from autotrade_v3.strategy_core.indicators.signals import align_confirmed, ensure_dt
from autotrade_v3.strategy_core.indicators.smc_ob import OBConfig, _confirmed_pivots, ob_context
from autotrade_v3.strategy_core.indicators.smc_structure import smc_direction

M15_MS = 900_000
H1_MS = 3_600_000
H4_MS = 14_400_000

MARKET_REGIME_ORDER: tuple[str, ...] = (
    "TĂNG_BÌNH_THƯỜNG",
    "TĂNG_BIẾN_ĐỘNG_CAO",
    "GIẢM_BÌNH_THƯỜNG",
    "GIẢM_BIẾN_ĐỘNG_CAO",
    "ĐI_NGANG_THẤP_VỪA",
    "ĐI_NGANG_BIẾN_ĐỘNG_CAO",
)

ALL_REGIME_DIMENSIONS: tuple[str, ...] = (
    "che_do_thi_truong",
    "che_do_xu_huong",
    "che_do_bien_dong",
    "che_do_thanh_khoan",
    "phien_giao_dich",
    "cau_truc_smc",
    "quet_thanh_khoan_smc",
    "vung_cung_cau_smc",
)


def load_research_config(config_path: Path | None = None) -> dict[str, Any]:
    """Đọc cấu hình tham số cố định từ config.json."""
    p = config_path or DEFAULT_CONFIG_PATH
    data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    return data


def validate_m15_continuity(df_m15: pd.DataFrame, *, allow_empty: bool = False) -> pd.DataFrame:
    """Kiểm tra chuỗi nến M15 liên tục, tăng dần, không trùng hoặc thiếu nến giữa chừng."""
    d = ensure_dt(df_m15)
    if d.empty:
        if allow_empty:
            return d
        raise ValueError("Chuỗi nến M15 rỗng")
    t_ms = datetime_index_to_ms(pd.DatetimeIndex(d.index))
    if d.index.has_duplicates:
        raise ValueError("Chuỗi nến M15 bị trùng timestamp")
    if not d.index.is_monotonic_increasing:
        raise ValueError("Chuỗi nến M15 không tăng dần theo thời gian")
    if (t_ms % M15_MS).any():
        raise ValueError("Chuỗi nến M15 không khớp biên 15 phút UTC")
    if len(t_ms) > 1 and (t_ms[1:] - t_ms[:-1] != M15_MS).any():
        raise ValueError("Chuỗi nến M15 bị thiếu nến (gapped) ở giữa chuỗi")
    return d


def _resample_completed_bars(df_m15: pd.DataFrame, rule: str, expected_m15_count: int) -> pd.DataFrame:
    """Tổng hợp nến TF lớn từ M15, chỉ giữ các nến ĐÃ ĐÓNG ĐẦY ĐỦ (tránh nhìn trước/partial bar)."""
    d = ensure_dt(df_m15)
    if d.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    agg = (
        d[["open", "high", "low", "close", "volume"]]
        .resample(rule, label="left", closed="left")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
    )
    counts = d["close"].resample(rule, label="left", closed="left").count()
    valid = agg.dropna(subset=["open", "high", "low", "close"]).copy()
    valid = valid.loc[counts.reindex(valid.index).fillna(0).astype(int) == expected_m15_count]
    return valid


def _compute_recent_bos_direction(
    df_1h: pd.DataFrame,
    pivot_len: int,
    recent_bars: int,
) -> pd.Series:
    """Xác định hướng phá cấu trúc (BOS) gần nhất chỉ dùng pivot đã xác nhận <= t."""
    ph, pl = _confirmed_pivots(df_1h, pivot_len)
    closes = df_1h["close"].to_numpy(dtype=float)
    n = len(df_1h)
    out = np.zeros(n, dtype=np.int8)

    sh = np.nan
    sl = np.nan
    sh_broken = False
    sl_broken = False
    last_dir = 0
    last_bar = -10_000

    for i in range(n):
        if np.isfinite(ph[i]):
            sh = ph[i]
            sh_broken = False
        if np.isfinite(pl[i]):
            sl = pl[i]
            sl_broken = False
        if np.isfinite(sh) and not sh_broken and closes[i] > sh:
            sh_broken = True
            last_dir = 1
            last_bar = i
        if np.isfinite(sl) and not sl_broken and closes[i] < sl:
            sl_broken = True
            last_dir = -1
            last_bar = i
        if (i - last_bar) <= recent_bars:
            out[i] = last_dir
    return pd.Series(out, index=df_1h.index, name="bos_recent_dir")


def _classify_session_utc(hour_utc: int, sessions_cfg: dict[str, list[int]]) -> str:
    for label, bounds in sessions_cfg.items():
        if int(bounds[0]) <= int(hour_utc) < int(bounds[1]):
            return str(label)
    return "PHIÊN_Á"


def compute_causal_hourly_regimes(
    df_m15: pd.DataFrame,
    *,
    config: dict[str, Any] | None = None,
    enforce_continuity: bool = True,
) -> pd.DataFrame:
    """Tính bảng đặc trưng chế độ thị trường & SMC theo từng nến 1H đóng hoàn toàn.

    Mọi phép tính tại thời điểm đóng nến `close_ms` chỉ dùng nến có thời gian đóng `<= close_ms`.
    """
    cfg = config or load_research_config()
    rp = cfg["regime_params"]

    d15 = validate_m15_continuity(df_m15) if enforce_continuity else ensure_dt(df_m15)
    d1 = _resample_completed_bars(d15, "1h", expected_m15_count=4)
    d4 = _resample_completed_bars(d15, "4h", expected_m15_count=16)

    if d1.empty:
        return pd.DataFrame()

    open_ms_1h = datetime_index_to_ms(pd.DatetimeIndex(d1.index))
    close_ms_1h = open_ms_1h + H1_MS - 1

    # 1. Xu hướng nhiều khung (1H + 4H đã đóng)
    ema_fast_1h = ema(d1["close"], int(rp["ema_fast_1h"]))
    ema_slow_1h = ema(d1["close"], int(rp["ema_slow_1h"]))
    if d4.empty:
        ema_slow_4h_1h = pd.Series(np.nan, index=d1.index, dtype=float)
        slope_4h_1h = pd.Series(np.nan, index=d1.index, dtype=float)
    else:
        ema_slow_4h_raw = ema(d4["close"], int(rp["ema_slow_4h"]))
        slope_bars = int(rp["slope_4h_bars"])
        prev_ema4 = ema_slow_4h_raw.shift(slope_bars)
        slope_4h_raw = (ema_slow_4h_raw - prev_ema4) / prev_ema4 * 100.0
        ema_slow_4h_1h = align_confirmed(ema_slow_4h_raw, pd.DatetimeIndex(d1.index), "4h")
        slope_4h_1h = align_confirmed(slope_4h_raw, pd.DatetimeIndex(d1.index), "4h")

    # 2. Biến động (ATR tương đối + phân vị biến động trượt)
    atr_p = int(rp["atr_period"])
    atr_sma_p = int(rp["atr_sma_period"])
    q_win = int(rp["atr_quantile_window"])
    atr14 = atr(d1, atr_p)
    atr_pct_1h = atr14 / d1["close"].replace(0.0, np.nan) * 100.0
    atr_sma50 = sma(atr14, atr_sma_p)
    atr_ratio_50 = atr14 / atr_sma50.replace(0.0, np.nan)
    atr_pct_rank = atr_pct_1h.rolling(q_win, min_periods=min(100, q_win)).rank(pct=True)

    # 3. Thanh khoản / Khối lượng tương đối
    vol_p = int(rp["volume_sma_period"])
    vol_sma = sma(d1["volume"], vol_p)
    vol_ratio = d1["volume"] / vol_sma.replace(0.0, np.nan)

    # 4. SMC đơn giản (cấu trúc, quét thanh khoản, vùng cung/cầu OB)
    smc_dir_s = smc_direction(d1, swing_len=int(rp["smc_swing_len"]), confluence=False)
    bos_recent_s = _compute_recent_bos_direction(
        d1,
        pivot_len=int(rp["ob_pivot_len"]),
        recent_bars=int(rp["smc_bos_recent_bars"]),
    )
    ob_cfg = OBConfig(
        pivot_len=int(rp["ob_pivot_len"]),
        search_bars=int(rp["ob_search_bars"]),
        max_age=int(rp["ob_max_age"]),
        danger_atr=0.0,
    )
    ob_df = ob_context(d1, ob_cfg)

    sweep_lb = int(rp["smc_sweep_lookback"])
    prev_high_n = d1["high"].shift(1).rolling(sweep_lb, min_periods=sweep_lb).max()
    prev_low_n = d1["low"].shift(1).rolling(sweep_lb, min_periods=sweep_lb).min()

    # Phân loại từng dòng bằng tham số khóa cố định
    slope_th = float(rp["trend_slope_threshold_pct"])
    v_low = float(rp["vol_ratio_low"])
    v_high = float(rp["vol_ratio_high"])
    l_low = float(rp["liq_ratio_low"])
    l_high = float(rp["liq_ratio_high"])
    ob_prox = float(rp["ob_proximity_atr"])
    sessions_cfg = dict(rp["sessions_utc"])

    closes = d1["close"].to_numpy(dtype=float)
    highs = d1["high"].to_numpy(dtype=float)
    lows = d1["low"].to_numpy(dtype=float)
    e50_arr = ema_fast_1h.to_numpy(dtype=float)
    e150_1h_arr = ema_slow_1h.to_numpy(dtype=float)
    e150_4h_arr = ema_slow_4h_1h.to_numpy(dtype=float)
    slp_4h_arr = slope_4h_1h.to_numpy(dtype=float)
    atr_arr = atr14.to_numpy(dtype=float)
    atr_ratio_arr = atr_ratio_50.to_numpy(dtype=float)
    vol_ratio_arr = vol_ratio.to_numpy(dtype=float)
    smc_arr = smc_dir_s.to_numpy(dtype=int)
    bos_arr = bos_recent_s.to_numpy(dtype=int)
    ph_n_arr = prev_high_n.to_numpy(dtype=float)
    pl_n_arr = prev_low_n.to_numpy(dtype=float)
    bull_lo_arr = ob_df["bull_ob_low"].to_numpy(dtype=float)
    bull_hi_arr = ob_df["bull_ob_high"].to_numpy(dtype=float)
    bear_lo_arr = ob_df["bear_ob_low"].to_numpy(dtype=float)
    bear_hi_arr = ob_df["bear_ob_high"].to_numpy(dtype=float)

    n = len(d1)
    trend_lbl: list[str] = []
    vol_lbl: list[str] = []
    liq_lbl: list[str] = []
    sess_lbl: list[str] = []
    smc_lbl: list[str] = []
    sweep_lbl: list[str] = []
    zone_lbl: list[str] = []
    regime_lbl: list[str] = []

    hours = pd.DatetimeIndex(d1.index).hour
    for i in range(n):
        c_val = closes[i]
        e1_val = e150_1h_arr[i]
        e4_val = e150_4h_arr[i]
        slp_val = slp_4h_arr[i]

        if not (np.isfinite(c_val) and np.isfinite(e1_val) and np.isfinite(e4_val) and np.isfinite(slp_val)):
            t_label = "KHÔNG_XÁC_ĐỊNH"
        elif c_val > e1_val and c_val > e4_val and slp_val >= slope_th:
            t_label = "XU_HƯỚNG_TĂNG"
        elif c_val < e1_val and c_val < e4_val and slp_val <= -slope_th:
            t_label = "XU_HƯỚNG_GIẢM"
        else:
            t_label = "ĐI_NGANG"
        trend_lbl.append(t_label)

        ar_val = atr_ratio_arr[i]
        if not np.isfinite(ar_val):
            v_label = "KHÔNG_XÁC_ĐỊNH"
        elif ar_val < v_low:
            v_label = "BIẾN_ĐỘNG_THẤP"
        elif ar_val <= v_high:
            v_label = "BIẾN_ĐỘNG_VỪA"
        else:
            v_label = "BIẾN_ĐỘNG_CAO"
        vol_lbl.append(v_label)

        lr_val = vol_ratio_arr[i]
        if not np.isfinite(lr_val):
            l_label = "KHÔNG_XÁC_ĐỊNH"
        elif lr_val < l_low:
            l_label = "THANH_KHOẢN_THẤP"
        elif lr_val <= l_high:
            l_label = "THANH_KHOẢN_BÌNH_THƯỜNG"
        else:
            l_label = "THANH_KHOẢN_ĐỘT_BIẾN"
        liq_lbl.append(l_label)

        sess_lbl.append(_classify_session_utc(int(hours[i]), sessions_cfg))

        sd_i = int(smc_arr[i])
        bd_i = int(bos_arr[i])
        if sd_i == 1 or (sd_i == 0 and bd_i == 1):
            s_label = "SMC_PHÁ_TĂNG"
        elif sd_i == -1 or (sd_i == 0 and bd_i == -1):
            s_label = "SMC_PHÁ_GIẢM"
        else:
            s_label = "SMC_TRUNG_TÍNH"
        smc_lbl.append(s_label)

        ph_v = ph_n_arr[i]
        pl_v = pl_n_arr[i]
        sw_h = np.isfinite(ph_v) and highs[i] > ph_v and c_val < ph_v
        sw_l = np.isfinite(pl_v) and lows[i] < pl_v and c_val > pl_v
        if sw_h and not sw_l:
            sw_label = "QUÉT_ĐỈNH_THANH_KHOẢN"
        elif sw_l and not sw_h:
            sw_label = "QUÉT_ĐÁY_THANH_KHOẢN"
        elif sw_h and sw_l:
            sw_label = "QUÉT_HAI_CHIỀU"
        else:
            sw_label = "KHÔNG_QUÉT"
        sweep_lbl.append(sw_label)

        a_val = atr_arr[i]
        dist_bull = np.inf
        dist_bear = np.inf
        if np.isfinite(a_val) and a_val > 0.0:
            if np.isfinite(bull_lo_arr[i]) and np.isfinite(bull_hi_arr[i]):
                if bull_lo_arr[i] <= c_val <= bull_hi_arr[i]:
                    dist_bull = 0.0
                else:
                    dist_bull = min(abs(c_val - bull_hi_arr[i]), abs(c_val - bull_lo_arr[i]))
            if np.isfinite(bear_lo_arr[i]) and np.isfinite(bear_hi_arr[i]):
                if bear_lo_arr[i] <= c_val <= bear_hi_arr[i]:
                    dist_bear = 0.0
                else:
                    dist_bear = min(abs(c_val - bear_hi_arr[i]), abs(c_val - bear_lo_arr[i]))
            max_dist = ob_prox * a_val
            near_bull = dist_bull <= max_dist
            near_bear = dist_bear <= max_dist
            if near_bull and (not near_bear or dist_bull <= dist_bear):
                z_label = "GẦN_VÙNG_CẦU"
            elif near_bear:
                z_label = "GẦN_VÙNG_CUNG"
            else:
                z_label = "XA_VÙNG_CUNG_CẦU"
        else:
            z_label = "KHÔNG_XÁC_ĐỊNH"
        zone_lbl.append(z_label)

        if t_label == "KHÔNG_XÁC_ĐỊNH" or v_label == "KHÔNG_XÁC_ĐỊNH":
            r_label = "KHÔNG_XÁC_ĐỊNH"
        elif t_label == "XU_HƯỚNG_TĂNG":
            r_label = "TĂNG_BIẾN_ĐỘNG_CAO" if v_label == "BIẾN_ĐỘNG_CAO" else "TĂNG_BÌNH_THƯỜNG"
        elif t_label == "XU_HƯỚNG_GIẢM":
            r_label = "GIẢM_BIẾN_ĐỘNG_CAO" if v_label == "BIẾN_ĐỘNG_CAO" else "GIẢM_BÌNH_THƯỜNG"
        else:
            r_label = "ĐI_NGANG_BIẾN_ĐỘNG_CAO" if v_label == "BIẾN_ĐỘNG_CAO" else "ĐI_NGANG_THẤP_VỪA"
        regime_lbl.append(r_label)

    out = pd.DataFrame(
        {
            "open_ms": open_ms_1h,
            "close_ms": close_ms_1h,
            "close": closes,
            "ema50_1h": e50_arr,
            "ema150_1h": e150_1h_arr,
            "ema150_4h": e150_4h_arr,
            "slope_4h_pct": slp_4h_arr,
            "atr14_1h": atr_arr,
            "atr_pct_1h": atr_pct_1h.to_numpy(dtype=float),
            "atr_ratio_50": atr_ratio_arr,
            "atr_pct_rank_720": atr_pct_rank.to_numpy(dtype=float),
            "vol_ratio_20": vol_ratio_arr,
            "smc_dir": smc_arr,
            "bos_recent_dir": bos_arr,
            "che_do_xu_huong": trend_lbl,
            "che_do_bien_dong": vol_lbl,
            "che_do_thanh_khoan": liq_lbl,
            "phien_giao_dich": sess_lbl,
            "cau_truc_smc": smc_lbl,
            "quet_thanh_khoan_smc": sweep_lbl,
            "vung_cung_cau_smc": zone_lbl,
            "che_do_thi_truong": regime_lbl,
        },
        index=d1.index,
    )
    return out


def map_timestamp_to_regime(
    hourly_regimes: pd.DataFrame,
    timestamp_ms: int,
    *,
    sessions_cfg: dict[str, list[int]] | None = None,
) -> dict[str, Any]:
    """Ánh xạ 1 mốc thời gian `timestamp_ms` sang đúng nến 1H đã đóng gần nhất (`close_ms <= timestamp_ms`)."""
    if hourly_regimes.empty:
        raise ValueError("Bảng đặc trưng chế độ thị trường 1H đang rỗng")
    close_arr = hourly_regimes["close_ms"].to_numpy(dtype=np.int64)
    idx = int(np.searchsorted(close_arr, int(timestamp_ms), side="right")) - 1
    if idx < 0:
        raise ValueError(f"Không có nến 1H đóng trước hoặc tại timestamp_ms={timestamp_ms}")
    row = hourly_regimes.iloc[idx]
    dt_utc = pd.to_datetime(int(timestamp_ms), unit="ms", utc=True)
    sess = (
        _classify_session_utc(int(dt_utc.hour), sessions_cfg)
        if sessions_cfg is not None
        else str(row["phien_giao_dich"])
    )
    return {
        "regime_candle_close_ms": int(row["close_ms"]),
        "che_do_thi_truong": str(row["che_do_thi_truong"]),
        "che_do_xu_huong": str(row["che_do_xu_huong"]),
        "che_do_bien_dong": str(row["che_do_bien_dong"]),
        "che_do_thanh_khoan": str(row["che_do_thanh_khoan"]),
        "phien_giao_dich": sess,
        "cau_truc_smc": str(row["cau_truc_smc"]),
        "quet_thanh_khoan_smc": str(row["quet_thanh_khoan_smc"]),
        "vung_cung_cau_smc": str(row["vung_cung_cau_smc"]),
        "atr_ratio_50": float(row["atr_ratio_50"]) if np.isfinite(row["atr_ratio_50"]) else None,
        "atr_pct_rank_720": float(row["atr_pct_rank_720"]) if np.isfinite(row["atr_pct_rank_720"]) else None,
        "vol_ratio_20": float(row["vol_ratio_20"]) if np.isfinite(row["vol_ratio_20"]) else None,
        "slope_4h_pct": float(row["slope_4h_pct"]) if np.isfinite(row["slope_4h_pct"]) else None,
    }


def attach_regimes_to_trades_df(
    trades_df: pd.DataFrame,
    hourly_by_symbol: dict[str, pd.DataFrame],
    *,
    config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Gắn nhãn chế độ thị trường tại thời điểm đóng nến tín hiệu (`signal_close_ms`) cho từng lệnh.

    Nếu `signal_close_ms` không có trong bảng lệnh, dùng `entry_time_ms - 1` (đều trước thời điểm vào lệnh).
    """
    cfg = config or load_research_config()
    sessions_cfg = dict(cfg["regime_params"]["sessions_utc"])
    df = trades_df.copy()
    if df.empty:
        for col in ALL_REGIME_DIMENSIONS:
            df[col] = []
        return df

    close_arrays = {
        sym: hdf["close_ms"].to_numpy(dtype=np.int64)
        for sym, hdf in hourly_by_symbol.items()
    }

    records: dict[str, list[Any]] = {col: [] for col in ALL_REGIME_DIMENSIONS}
    records["regime_candle_close_ms"] = []

    use_sig_ts = "signal_close_ms" in df.columns
    for _, row in df.iterrows():
        sym = str(row["symbol"])
        hdf = hourly_by_symbol[sym]
        c_arr = close_arrays[sym]
        ts = int(row["signal_close_ms"]) if use_sig_ts else (int(row["entry_time_ms"]) - 1)
        idx = int(np.searchsorted(c_arr, ts, side="right")) - 1
        if idx < 0:
            raise ValueError(f"Thiếu nến 1H đóng trước tín hiệu {sym} ts={ts}")
        hrow = hdf.iloc[idx]
        dt_utc = pd.to_datetime(ts, unit="ms", utc=True)

        records["regime_candle_close_ms"].append(int(hrow["close_ms"]))
        records["che_do_thi_truong"].append(str(hrow["che_do_thi_truong"]))
        records["che_do_xu_huong"].append(str(hrow["che_do_xu_huong"]))
        records["che_do_bien_dong"].append(str(hrow["che_do_bien_dong"]))
        records["che_do_thanh_khoan"].append(str(hrow["che_do_thanh_khoan"]))
        records["phien_giao_dich"].append(_classify_session_utc(int(dt_utc.hour), sessions_cfg))
        records["cau_truc_smc"].append(str(hrow["cau_truc_smc"]))
        records["quet_thanh_khoan_smc"].append(str(hrow["quet_thanh_khoan_smc"]))
        records["vung_cung_cau_smc"].append(str(hrow["vung_cung_cau_smc"]))

    for k, vals in records.items():
        df[k] = vals
    return df
