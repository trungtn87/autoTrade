"""Bộ nạp dữ liệu chỉ đọc từ kho local D:/AutoTrade/data/bingx/ với xác minh SHA-256 theo manifest."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from autotrade_v3.collector_v3.models import sha256_file, utc_iso


class ManifestVerificationError(RuntimeError):
    """Lỗi khi xác minh SHA-256 hoặc số dòng theo manifest thất bại."""


@dataclass(frozen=True)
class SourceCoverage:
    source: str
    path: str
    row_count: int
    sha256: str
    min_ts_ms: int | None
    max_ts_ms: int | None
    min_ts_utc: str | None
    max_ts_utc: str | None
    status: str
    provenance: str
    note: str = ""


@dataclass
class LocalStoreData:
    data_root: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    # 6 bảng autotrade_v3 (live_events_v3)
    strategy_signals: list[dict[str, Any]] = field(default_factory=list)
    execution_orders: list[dict[str, Any]] = field(default_factory=list)
    execution_results: list[dict[str, Any]] = field(default_factory=list)
    execution_finances: list[dict[str, Any]] = field(default_factory=list)
    control_logs: list[dict[str, Any]] = field(default_factory=list)
    candles_15m: list[dict[str, Any]] = field(default_factory=list)
    # 4 bảng autotrade_analytics.bingx_* (bằng chứng phụ)
    bingx_orders: list[dict[str, Any]] = field(default_factory=list)
    bingx_fills: list[dict[str, Any]] = field(default_factory=list)
    bingx_income: list[dict[str, Any]] = field(default_factory=list)
    bingx_positions: list[dict[str, Any]] = field(default_factory=list)
    # Dữ liệu thị trường M1, funding, contract rules
    candles_1m: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    funding_rates: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    contract_rules: list[dict[str, Any]] = field(default_factory=list)
    # Phủ dữ liệu
    coverage: list[SourceCoverage] = field(default_factory=list)


def _parse_iso_ms(val: Any) -> int | None:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return int(val)
    raw = str(val).strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _min_max_ts(rows: list[dict[str, Any]], col: str, is_iso: bool = False) -> tuple[int | None, int | None]:
    vals: list[int] = []
    for r in rows:
        v = r.get(col)
        if v is None or v == "":
            continue
        ts = _parse_iso_ms(v) if is_iso else int(v)
        if ts is not None:
            vals.append(ts)
    if not vals:
        return None, None
    return min(vals), max(vals)


def verify_manifest_parquets(data_root: Path, manifest: dict[str, Any]) -> dict[str, str]:
    """Xác minh SHA-256 và row_count của mọi file Parquet trong manifest. Trả về map rel_path -> sha256."""
    verified_hashes: dict[str, str] = {}
    sources = manifest.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ManifestVerificationError("Manifest thiếu trường 'sources' hoặc rỗng.")

    for src_name, src_info in sources.items():
        parquet_files = src_info.get("parquet_files", [])
        for pf in parquet_files:
            rel_path = str(pf["path"])
            expected_sha = str(pf["sha256"]).lower()
            expected_rows = int(pf["row_count"])
            full_path = data_root / rel_path
            if not full_path.is_file():
                raise ManifestVerificationError(f"Thiếu file Parquet trong kho ({src_name}): {full_path}")
            actual_sha = sha256_file(full_path).lower()
            if actual_sha != expected_sha:
                raise ManifestVerificationError(
                    f"Sai SHA-256 tại {rel_path}: expected={expected_sha}, actual={actual_sha}"
                )
            meta = pq.read_metadata(full_path)
            if int(meta.num_rows) != expected_rows:
                raise ManifestVerificationError(
                    f"Sai số dòng tại {rel_path}: expected={expected_rows}, actual={meta.num_rows}"
                )
            verified_hashes[rel_path] = actual_sha
    return verified_hashes


def _read_parquet_rows(path: Path) -> list[dict[str, Any]]:
    return pq.read_table(path).to_pylist()


def load_local_store(data_root: Path | str = Path("D:/AutoTrade/data/bingx")) -> LocalStoreData:
    """Đọc toàn bộ kho dữ liệu local sau khi xác minh SHA-256 theo latest_manifest.json."""
    root = Path(data_root)
    manifest_path = root / "manifests" / "latest_manifest.json"
    if not manifest_path.is_file():
        raise ManifestVerificationError(f"Không tìm thấy manifest: {manifest_path}")

    manifest_sha = sha256_file(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verified_hashes = verify_manifest_parquets(root, manifest)

    # Đọc 6 bảng live_events_v3
    strategy_signals = _read_parquet_rows(root / "live_events" / "strategy_signals.parquet")
    execution_orders = _read_parquet_rows(root / "live_events" / "execution_orders.parquet")
    execution_results = _read_parquet_rows(root / "live_events" / "execution_results.parquet")
    execution_finances = _read_parquet_rows(root / "live_events" / "execution_finances.parquet")
    control_logs = _read_parquet_rows(root / "live_events" / "control_logs.parquet")
    candles_15m = _read_parquet_rows(root / "live_events" / "candles.parquet")

    # Đọc 4 bảng bingx_* (bằng chứng phụ)
    bingx_orders = _read_parquet_rows(root / "execution" / "orders" / "orders.parquet")
    bingx_fills = _read_parquet_rows(root / "execution" / "fills" / "fills.parquet")
    bingx_income = _read_parquet_rows(root / "execution" / "income" / "income.parquet")
    bingx_positions: list[dict[str, Any]] = []  # Rỗng thật theo manifest (status=GAP)

    # Đọc M1, funding, contract_rules
    candles_1m = {
        "BTC-USDT": _read_parquet_rows(root / "market" / "candles_1m" / "BTC-USDT.parquet"),
        "ETH-USDT": _read_parquet_rows(root / "market" / "candles_1m" / "ETH-USDT.parquet"),
    }
    funding_rates = {
        "BTC-USDT": _read_parquet_rows(root / "market" / "funding" / "BTC-USDT.parquet"),
        "ETH-USDT": _read_parquet_rows(root / "market" / "funding" / "ETH-USDT.parquet"),
    }
    contract_rules = _read_parquet_rows(root / "market" / "contract_rules" / "contracts.parquet")

    coverage: list[SourceCoverage] = []

    def _add_cov(
        source: str,
        rel_path: str,
        rows: list[dict[str, Any]],
        ts_col: str | None,
        is_iso: bool = False,
        status: str = "COMPLETE",
        provenance: str = "EXACT",
        note: str = "",
    ) -> None:
        if ts_col is not None and rows:
            mn, mx = _min_max_ts(rows, ts_col, is_iso=is_iso)
        else:
            mn, mx = None, None
        coverage.append(
            SourceCoverage(
                source=source,
                path=rel_path,
                row_count=len(rows),
                sha256=verified_hashes.get(rel_path, ""),
                min_ts_ms=mn,
                max_ts_ms=mx,
                min_ts_utc=utc_iso(mn) if mn is not None else None,
                max_ts_utc=utc_iso(mx) if mx is not None else None,
                status=status,
                provenance=provenance,
                note=note,
            )
        )

    _add_cov("live_events_v3.strategy_signals", "live_events/strategy_signals.parquet", strategy_signals, "signal_time", is_iso=True, note="Nguồn chính tín hiệu V3")
    _add_cov("live_events_v3.execution_orders", "live_events/execution_orders.parquet", execution_orders, "created_at", is_iso=True, note="Nguồn chính lệnh V3")
    _add_cov("live_events_v3.execution_results", "live_events/execution_results.parquet", execution_results, "closed_at", is_iso=True, note="Nguồn chính kết quả đóng từng phần V3")
    _add_cov("live_events_v3.execution_finances", "live_events/execution_finances.parquet", execution_finances, "updated_at", is_iso=True, note="Nguồn chính tài chính V3 (funding=NULL theo thiết kế)")
    _add_cov("live_events_v3.control_logs", "live_events/control_logs.parquet", control_logs, "created_at", is_iso=True, note="Nhật ký hệ thống V3 (dùng suy ra VPS_TẮT)")
    _add_cov("live_events_v3.candles", "live_events/candles.parquet", candles_15m, "open_time", is_iso=False, note="Nến 15m cache live (BTC=3400, ETH=12000)")
    _add_cov("bingx_orders", "execution/orders/orders.parquet", bingx_orders, "created_ms", is_iso=False, note="Bằng chứng phụ đến 14/11 15:36 UTC (trước PR #119)")
    _add_cov("bingx_fills", "execution/fills/fills.parquet", bingx_fills, "timestamp_ms", is_iso=False, note="Bằng chứng phụ đến 14/11 15:36 UTC (trước PR #119)")
    _add_cov("bingx_income", "execution/income/income.parquet", bingx_income, "timestamp_ms", is_iso=False, note="Bằng chứng phụ đến 14/11 16:00 UTC (trước PR #119)")
    _add_cov("bingx_position_history", "execution/positions/", bingx_positions, None, status="GAP", note="Bảng nguồn rỗng thật (0 dòng)")
    _add_cov("market_candles_1m.BTC-USDT", "market/candles_1m/BTC-USDT.parquet", candles_1m["BTC-USDT"], "open_time_ms", is_iso=False, note="Nến M1 liên tục")
    _add_cov("market_candles_1m.ETH-USDT", "market/candles_1m/ETH-USDT.parquet", candles_1m["ETH-USDT"], "open_time_ms", is_iso=False, note="Nến M1 liên tục")
    _add_cov("market_funding.BTC-USDT", "market/funding/BTC-USDT.parquet", funding_rates["BTC-USDT"], "funding_time_ms", is_iso=False, note="Lãi suất funding 8h (dùng ước lượng ESTIMATED)")
    _add_cov("market_funding.ETH-USDT", "market/funding/ETH-USDT.parquet", funding_rates["ETH-USDT"], "funding_time_ms", is_iso=False, note="Lãi suất funding 8h (dùng ước lượng ESTIMATED)")
    _add_cov("market_contract_rules", "market/contract_rules/contracts.parquet", contract_rules, "snapshot_ms", is_iso=False, note="Quy tắc hợp đồng BingX")

    return LocalStoreData(
        data_root=root,
        manifest=manifest,
        manifest_sha256=manifest_sha,
        strategy_signals=strategy_signals,
        execution_orders=execution_orders,
        execution_results=execution_results,
        execution_finances=execution_finances,
        control_logs=control_logs,
        candles_15m=candles_15m,
        bingx_orders=bingx_orders,
        bingx_fills=bingx_fills,
        bingx_income=bingx_income,
        bingx_positions=bingx_positions,
        candles_1m=candles_1m,
        funding_rates=funding_rates,
        contract_rules=contract_rules,
        coverage=coverage,
    )
