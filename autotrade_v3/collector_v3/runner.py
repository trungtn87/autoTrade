"""Điều phối chạy thu dữ liệu độc lập cho từng nguồn trong collector_v3."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .http_client import BingXPublicClient
from .models import (
    DEFAULT_SYMBOLS,
    SCHEMA_VERSION,
    RunManifest,
    SourceResult,
    parse_since_ms,
    utc_iso,
    utc_now_ms,
)
from .sources.base import BaseSource, CollectContext
from .sources.bingx_public import (
    BingXCandles1mSource,
    BingXContractRulesSource,
    BingXFundingSource,
    BingXMarkPriceSource,
)
from .sources.supabase_ro import (
    SupabaseAutotradeV3Source,
    SupabaseReadOnlyReader,
    build_analytics_sources,
    is_fatal_db_connection_error,
)
from .storage import (
    CollectorLock,
    StateStore,
    redact_secrets,
    save_run_manifest_and_coverage,
    setup_logger,
)

DEFAULT_DATA_DIR = Path(r"D:\AutoTrade\data\bingx")


def build_default_sources() -> list[BaseSource]:
    """Danh sách tất cả nguồn độc lập (bỏ qua bid/ask spread theo quyết định 15/11)."""
    sources: list[BaseSource] = [
        BingXCandles1mSource(),
        BingXMarkPriceSource(),
        BingXFundingSource(),
        BingXContractRulesSource(),
    ]
    sources.extend(build_analytics_sources())
    sources.append(SupabaseAutotradeV3Source())
    return sources


def run_collector(
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    dry_run: bool = False,
    since: str | int | None = None,
    only: Sequence[str] | None = None,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    http_client: Any = None,
    db_reader: Any = None,
    sources: Sequence[BaseSource] | None = None,
    max_pages_per_run: int | None = None,
    now_ms: int | None = None,
) -> RunManifest:
    """Chạy thu dữ liệu cho các nguồn đã chọn, cô lập lỗi từng nguồn."""
    data_dir = Path(data_dir)
    logger = setup_logger(data_dir / "logs")
    since_ms = parse_since_ms(since)
    run_now_ms = now_ms if now_ms is not None else utc_now_ms()
    collected_at = utc_iso(run_now_ms)
    run_id = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")

    all_sources = list(sources) if sources is not None else build_default_sources()
    if only:
        wanted = {s.strip() for item in only for s in item.split(",") if s.strip()}
        selected = [s for s in all_sources if s.name in wanted]
        unknown = wanted - {s.name for s in all_sources}
        if unknown:
            raise ValueError(f"Nguồn không tồn tại: {', '.join(sorted(unknown))}")
    else:
        selected = all_sources

    client = http_client if http_client is not None else BingXPublicClient()
    reader = db_reader if db_reader is not None else SupabaseReadOnlyReader()

    lock_path = data_dir / "manifests" / ".collector.lock"
    state_store = StateStore(data_dir / "manifests" / "state.json")

    with CollectorLock(lock_path):
        state = state_store.load()
        sources_state: dict[str, Any] = state.setdefault("sources", {})
        results: dict[str, SourceResult] = {}
        db_abort_state: dict[str, str | None] = {"reason": None}

        logger.info(
            "Bắt đầu đợt thu %s (dry_run=%s, số nguồn=%d)",
            run_id,
            dry_run,
            len(selected),
        )

        for src in selected:
            src_state = dict(sources_state.get(src.name) or {})
            ctx = CollectContext(
                data_dir=data_dir,
                source_state=src_state,
                dry_run=dry_run,
                now_ms=run_now_ms,
                collected_at=collected_at,
                since_ms=since_ms,
                symbols=symbols,
                http_client=client,
                db_reader=reader,
                max_pages_per_run=max_pages_per_run,
                logger=logger,
                db_abort_state=db_abort_state,
            )
            try:
                res = src.collect(ctx)
            except Exception as exc:
                safe_err = redact_secrets(f"{type(exc).__name__}: {exc}")
                if is_fatal_db_connection_error(exc) and not db_abort_state.get("reason"):
                    db_abort_state["reason"] = safe_err
                logger.exception("Nguồn %s gặp lỗi độc lập: %s", src.name, safe_err)
                res = SourceResult(
                    source=src.name,
                    status="INCOMPLETE",
                    provenance="UNRESOLVED",
                    collected_at=collected_at,
                    error=safe_err,
                    message_vi=f"Nguồn {src.name} lỗi: {safe_err}",
                )

            results[src.name] = res
            if not dry_run:
                src_state["last_run_at"] = collected_at
                src_state["last_status"] = res.status
                sources_state[src.name] = src_state
                state_store.save(state)

            logger.info("[%s] %s - %s", res.source, res.status, res.message_vi)

        statuses = {r.status for r in results.values()}
        if "INCOMPLETE" in statuses:
            overall = "INCOMPLETE"
        elif "GAP" in statuses:
            overall = "GAP"
        else:
            overall = "COMPLETE"

        manifest = RunManifest(
            run_id=run_id,
            collected_at=collected_at,
            dry_run=dry_run,
            overall_status=overall,
            sources=results,
            schema_version=SCHEMA_VERSION,
            message_vi=f"Đợt thu {run_id} kết thúc với trạng thái {overall} ({len(results)} nguồn).",
        )

        if not dry_run:
            save_run_manifest_and_coverage(data_dir, manifest)

        return manifest
