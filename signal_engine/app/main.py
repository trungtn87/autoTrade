from __future__ import annotations

import json
import logging
import threading
import time

import pandas as pd
from contextlib import asynccontextmanager
from dataclasses import asdict, replace

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Header, HTTPException

from .bingx_market import BingXApiError, BingXMarketClient, closed_only
from .data_validation import DataValidationError, validate_15m_candles
from .config import Settings, safe_config_snapshot, validate_settings
from .final14_executor import Final14Executor
from .discord_diag import (
    install_discord_log_handler,
    send_discord_scan_alert,
    send_discord_scan_summary,
    send_discord_startup_test,
)
from .state import SignalState
from .final14_exact_strategy import Signal, combo_readiness, scan_latest, strategy_static_snapshot
from .self_test import run_self_test, run_startup_self_test
from .final14_positions import is_case_active, refresh_symbol, register_execution, snapshot as final14_position_snapshot
from .timeframes import aggregate_15m
from .warmup_seed import load_warmup_seed

settings = Settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
# httpx/httpcore INFO records include the full request URL, which may contain
# Discord/order webhook secrets. Keep only warnings/errors from these libraries.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("autotrade")

market = BingXMarketClient(base_url=settings.bingx_base_url, api_key=settings.bingx_api_key, api_secret=settings.bingx_api_secret)
state = SignalState(settings.state_db, settings.database_url)
executor = Final14Executor(settings)
scan_lock = threading.Lock()
scheduler: BackgroundScheduler | None = None
last_scan_summary: dict = {"status": "not_run"}
MANUAL_ORDER_TEST_MODE = False
ONE_SHOT_BTC_BUY_TEST = False
ONE_SHOT_BTC_BUY_STATE_KEY = "one_shot_btc_buy_1usdt_x100_v1"
ONE_SHOT_ETH_BUY_TEST = False
ONE_SHOT_ETH_BUY_STATE_KEY = "one_shot_eth_buy_1usdt_x100_v1"
ONE_SHOT_BTC_CLOSE_TEST = False
ONE_SHOT_BTC_CLOSE_STATE_KEY = "one_shot_btc_close_long_v1"
ONE_SHOT_ETH_CLOSE_TEST = False
ONE_SHOT_ETH_CLOSE_STATE_KEY = "one_shot_eth_close_long_v1"


INTERVAL_15M_MS = 15 * 60_000
BINGX_COOLDOWN_STATE_KEY = "bingx_market_blocked_until_ms"


def _effective_bingx_blocked_until_ms() -> int:
    try:
        persisted = int(state.get_runtime_value(BINGX_COOLDOWN_STATE_KEY, "0") or 0)
    except Exception:
        persisted = 0
    market.set_blocked_until_ms(persisted)
    return max(int(market.blocked_until_ms), persisted)


def _fetch_15m_range(
    symbol: str,
    start_open: int,
    end_open: int,
    now_ms: int,
) -> pd.DataFrame:
    """Fetch a closed, UTC-aligned 15m range with backward pagination."""
    if end_open < start_open:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])

    pages: list[pd.DataFrame] = []
    cursor_end = int(end_open) + INTERVAL_15M_MS - 1
    wanted = ((int(end_open) - int(start_open)) // INTERVAL_15M_MS) + 1
    remaining = wanted

    while remaining > 0 and cursor_end >= start_open:
        page_limit = min(1000, remaining)
        page = closed_only(
            market.klines(
                symbol,
                "15m",
                page_limit,
                start_time=int(start_open),
                end_time=int(cursor_end),
            ),
            now_ms,
        )
        if page.empty:
            break

        page = page[
            (page["open_time"] >= int(start_open))
            & (page["open_time"] <= int(end_open))
        ].copy()
        if page.empty:
            break

        pages.append(page)
        earliest = int(page.iloc[0]["open_time"])
        if earliest <= start_open:
            break

        cursor_end = earliest - 1
        remaining = max(
            0,
            ((earliest - int(start_open)) // INTERVAL_15M_MS),
        )

    if not pages:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])

    return (
        pd.concat(pages, ignore_index=True)
        .sort_values("open_time")
        .drop_duplicates("open_time", keep="last")
        .reset_index(drop=True)
    )


def _recover_15m_validation_gap(symbol: str, m15: pd.DataFrame, now_ms: int, exc: DataValidationError) -> pd.DataFrame:
    details = getattr(exc, "details", {}) or {}
    if str(exc) != "15m cache contains a candle gap":
        raise exc

    start_open = details.get("expected_next_open")
    next_open = details.get("next_open")
    missing = int(details.get("missing_intervals", 0) or 0)
    if start_open is None or next_open is None or missing <= 0:
        raise exc

    end_open = int(next_open) - INTERVAL_15M_MS
    log.warning(
        "CANDLE_GAP_INTERNAL symbol=%s previous_open=%s expected_next=%s next_open=%s missing_intervals=%s",
        symbol,
        details.get("previous_open"),
        start_open,
        next_open,
        missing,
    )
    recovery = _fetch_15m_range(
        symbol,
        int(start_open),
        int(end_open),
        now_ms,
    )
    if recovery.empty:
        log.error(
            "RECOVERY_EMPTY symbol=%s start_open=%s end_open=%s missing_intervals=%s",
            symbol, start_open, end_open, missing,
        )
        raise exc

    state.upsert_candles(symbol, "15m", recovery)
    log.info(
        "RECOVERY_BACKFILL symbol=%s requested_missing=%s recovered=%s first_open=%s last_open=%s",
        symbol,
        missing,
        len(recovery),
        int(recovery.iloc[0]["open_time"]),
        int(recovery.iloc[-1]["open_time"]),
    )
    state.trim_candles(symbol, "15m", settings.candle_keep_15m)
    return state.load_candles(symbol, "15m")


def fetch_bundle(symbol: str):
    """Production data path: keep the latest candle current, then warm history gradually."""
    started = time.monotonic()
    now_ms = int(time.time() * 1000)
    cached_count = state.candle_count(symbol, "15m")
    bootstrap = cached_count == 0
    target = max(12000, int(settings.bootstrap_limit_15m))

    # Production seed: fill the deterministic warmup window from the canonical
    # BingX 15m snapshot bundled with the service before asking BingX for older
    # history. This avoids the exchange history boundary that can return an
    # empty page even though the request itself succeeds.
    if cached_count < target:
        try:
            seed = load_warmup_seed(symbol, target)
            if len(seed):
                before_seed = cached_count
                state.upsert_candles(symbol, "15m", seed)
                cached_count = state.candle_count(symbol, "15m")
                log.info(
                    "WARMUP_SEED_APPLIED symbol=%s seed_rows=%s cached_before=%s cached_after=%s first_open=%s last_open=%s",
                    symbol,
                    len(seed),
                    before_seed,
                    cached_count,
                    int(seed.iloc[0]["open_time"]),
                    int(seed.iloc[-1]["open_time"]),
                )
        except Exception as exc:
            log.exception("WARMUP_SEED_FAILED symbol=%s error=%s", symbol, exc)
            cached_count = state.candle_count(symbol, "15m")

    # Always refresh the latest closed candle first. This keeps live data current
    # even while the historical warmup is still being filled.
    log.info(
        "FETCH_START symbol=%s mode=%s cached_15m=%s live_limit=%s target=%s",
        symbol,
        "bootstrap" if bootstrap else ("warmup_backfill" if cached_count < target else "incremental"),
        cached_count,
        settings.live_limit_15m,
        target,
    )
    latest = closed_only(
        market.klines(symbol, "15m", settings.live_limit_15m),
        now_ms,
    )
    if len(latest):
        state.upsert_candles(symbol, "15m", latest)
        log.info(
            "FETCH_DATA symbol=%s received_closed=%s first_open=%s last_open=%s last_close=%s",
            symbol,
            len(latest),
            int(latest.iloc[0]["open_time"]),
            int(latest.iloc[-1]["open_time"]),
            int(latest.iloc[-1]["close_time"]),
        )
    else:
        log.warning("FETCH_DATA symbol=%s received_closed=0", symbol)

    # Historical warmup is intentionally incremental: at most one older page
    # per scheduled scan. This avoids hammering BingX and, critically, every
    # successful page is persisted immediately so progress is never lost.
    cached_count = state.candle_count(symbol, "15m")
    if cached_count < target:
        earliest = state.earliest_open_time(symbol, "15m")
        if earliest is not None:
            missing = target - cached_count
            page_limit = min(1000, missing)
            history_end = int(earliest) - 1
            history_start = max(
                0,
                int(earliest) - page_limit * INTERVAL_15M_MS,
            )
            log.info(
                "WARMUP_BACKFILL_START symbol=%s cached_15m=%s target=%s page_limit=%s start=%s end=%s",
                symbol, cached_count, target, page_limit, history_start, history_end,
            )
            older = closed_only(
                market.klines(
                    symbol,
                    "15m",
                    page_limit,
                    start_time=history_start,
                    end_time=history_end,
                ),
                now_ms,
            )
            if len(older):
                state.upsert_candles(symbol, "15m", older)
                log.info(
                    "WARMUP_BACKFILL_SAVED symbol=%s recovered=%s first_open=%s last_open=%s cached_after=%s",
                    symbol,
                    len(older),
                    int(older.iloc[0]["open_time"]),
                    int(older.iloc[-1]["open_time"]),
                    state.candle_count(symbol, "15m"),
                )
            else:
                log.warning(
                    "WARMUP_BACKFILL_EMPTY symbol=%s start=%s end=%s",
                    symbol, history_start, history_end,
                )

    state.trim_candles(symbol, "15m", settings.candle_keep_15m)
    m15 = state.load_candles(symbol, "15m")

    try:
        validation = validate_15m_candles(m15, now_ms)
    except DataValidationError as exc:
        if str(exc) == "15m cache contains a candle gap":
            m15 = _recover_15m_validation_gap(symbol, m15, now_ms, exc)
            validation = validate_15m_candles(m15, now_ms)
        else:
            log.error(
                "DATA_VALIDATION_FAILED symbol=%s error=%s details=%s",
                symbol, exc, json.dumps(getattr(exc, "details", {}) or {}, ensure_ascii=False),
            )
            raise

    log.info(
        "DATA_VALIDATION symbol=%s ok=true count=%s latest_open=%s latest_close=%s",
        symbol,
        validation.get("count"),
        validation.get("latest_open"),
        validation.get("latest_close"),
    )

    h1 = aggregate_15m(m15, 60)
    h4 = aggregate_15m(m15, 240)
    h6 = aggregate_15m(m15, 360)

    log.info(
        "DERIVED symbol=%s 15m=%s 1h=%s 4h=%s 6h=%s elapsed_ms=%s",
        symbol, len(m15), len(h1), len(h4), len(h6),
        round((time.monotonic() - started) * 1000, 1),
    )

    if min(len(m15), len(h1), len(h4), len(h6)) == 0:
        raise RuntimeError(
            f"Warmup incomplete for {symbol}: "
            f"15m={len(m15)} 1h={len(h1)} 4h={len(h4)} 6h={len(h6)}"
        )

    return now_ms, m15, h1, h4, h6, bootstrap, validation


def run_scan(execute: bool = True) -> dict:
    global last_scan_summary
    if not scan_lock.acquire(blocking=False):
        return {"status": "busy", "message": "scan already running"}
    started = time.time()
    summary = {
        "status": "ok",
        "dry_run": settings.dry_run,
        "execute": execute,
        "symbols": {},
        "started_at": started,
    }
    try:
        cooldown_until = _effective_bingx_blocked_until_ms()
        now_ms = int(time.time() * 1000)
        if cooldown_until > now_ms:
            remaining_sec = int((cooldown_until - now_ms + 999) / 1000)
            summary.update({
                "status": "bingx_cooldown",
                "retry_at_ms": cooldown_until,
                "cooldown_remaining_sec": remaining_sec,
                "elapsed_sec": round(time.time() - started, 3),
            })
            last_scan_summary = summary
            log.warning(
                "SCHEDULE_SKIP_BINGX_COOLDOWN retry_at_ms=%s remaining_sec=%s",
                cooldown_until, remaining_sec,
            )
            return summary

        if cooldown_until:
            # A persisted breaker has just expired. Verify the configured
            # symbols against BingX's contract list before resuming klines.
            # This is especially important after 109425, whose documented
            # meaning is unsupported/non-existent trading pair.
            try:
                supported = market.contract_symbols()
            except BingXApiError as exc:
                blocked_until = max(
                    int(exc.retry_at_ms or 0),
                    int(market.blocked_until_ms),
                    int(time.time() * 1000) + 60_000,
                )
                state.set_runtime_value(BINGX_COOLDOWN_STATE_KEY, str(blocked_until))
                summary.update({
                    "status": "bingx_recovery_check_error",
                    "retry_at_ms": blocked_until,
                    "elapsed_sec": round(time.time() - started, 3),
                    "recovery_error": str(exc),
                })
                last_scan_summary = summary
                log.error(
                    "BINGX_RECOVERY_CHECK_ERROR retry_at_ms=%s code=%s error=%s",
                    blocked_until, exc.code, exc,
                )
                return summary

            missing_symbols = sorted(set(settings.symbols) - set(supported))
            if missing_symbols:
                blocked_until = int(time.time() * 1000) + 16 * 60 * 1000
                market.set_blocked_until_ms(blocked_until)
                state.set_runtime_value(BINGX_COOLDOWN_STATE_KEY, str(blocked_until))
                summary.update({
                    "status": "bingx_symbol_guard_failed",
                    "retry_at_ms": blocked_until,
                    "missing_symbols": missing_symbols,
                    "elapsed_sec": round(time.time() - started, 3),
                })
                last_scan_summary = summary
                log.error(
                    "BINGX_SYMBOL_GUARD_FAILED missing=%s supported_count=%s retry_at_ms=%s",
                    missing_symbols, len(supported), blocked_until,
                )
                return summary

            log.warning(
                "BINGX_COOLDOWN_RECOVERY_CHECK ok=true configured=%s supported_count=%s",
                list(settings.symbols), len(supported),
            )
            state.set_runtime_value(BINGX_COOLDOWN_STATE_KEY, "0")

        for symbol in settings.symbols:
            symbol_started = time.monotonic()
            try:
                log.info("SYMBOL_SCAN_START symbol=%s execute=%s", symbol, execute)
                now_ms, m15, h1, h4, h6, bootstrap, data_validation = fetch_bundle(symbol)

                # Reconcile independent SEPARATE_ISOLATED positions before
                # evaluating new entries. This enforces one active position
                # per symbol+combo exactly like backtest.
                position_state = refresh_symbol(state, executor, symbol)

                readiness = combo_readiness(
                    m15, h1, h4, h6,
                    smc_swing_len=settings.smc_swing_len,
                    symbol=symbol,
                )
                ready_combos = sorted(
                    combo for combo, item in readiness.items() if item.get("ready")
                )
                skipped_combos = sorted(
                    combo for combo, item in readiness.items() if not item.get("ready")
                )
                log.info(
                    "COMBO_READINESS symbol=%s ready=%s skipped=%s frames={15m:%s,1h:%s,4h:%s,6h:%s}",
                    symbol, ready_combos, skipped_combos,
                    len(m15), len(h1), len(h4), len(h6),
                )

                calc_started = time.monotonic()
                signals = scan_latest(
                    symbol=symbol,
                    m15=m15,
                    h1=h1,
                    h4=h4,
                    h6=h6,
                    smc_mode=settings.smc_mode,
                    smc_swing_len=settings.smc_swing_len,
                    smc_confluence=settings.smc_confluence,
                    sl_pct=settings.sl_pct,
                    tp_pct=settings.tp_pct,
                    include_1h=True,
                )
                calc_ms = round((time.monotonic() - calc_started) * 1000, 1)
                log.info(
                    "SIGNAL_CALC symbol=%s signals=%s calc_ms=%s ids=%s",
                    symbol, len(signals), calc_ms, [s.event_id for s in signals],
                )
                symbol_result = {
                    "server_time": now_ms,
                    "latest_15m_close": int(m15.iloc[-1]["close_time"]),
                    "latest_1h_close": int(h1.iloc[-1]["close_time"]),
                    "market_source": "bingx_15m_only",
                    "state_backend": state.backend,
                    "bootstrap": bootstrap,
                    "data_validation": data_validation,
                    "cached_15m": len(m15),
                    "derived_1h": len(h1),
                    "derived_4h": len(h4),
                    "derived_6h": len(h6),
                    "combo_readiness": {
                        "ready": ready_combos,
                        "skipped": skipped_combos,
                    },
                    "signals": [],
                    "final14_positions": position_state,
                }
                for sig in signals:
                    item = asdict(sig)
                    item["event_id"] = sig.event_id

                    # Safety for a brand-new/rotated database: bootstrap loads
                    # historical context but must never execute a signal from
                    # data that existed before this engine instance had a
                    # persistent dedupe history. Trading resumes on the next
                    # incremental 15m cycle.
                    if bootstrap:
                        item["action"] = "bootstrap_suppressed"
                        item["reason"] = "first persistent-data warmup; execution starts next incremental candle"
                        symbol_result["signals"].append(item)
                        log.warning(
                            "BOOTSTRAP_SIGNAL_SUPPRESSED symbol=%s event_id=%s combo=%s side=%s tf=%s",
                            sig.symbol, sig.event_id, sig.combo, sig.side, sig.timeframe,
                        )
                        continue

                    expected_close = (
                        int(m15.iloc[-1]["close_time"])
                        if sig.timeframe == "15m"
                        else int(h1.iloc[-1]["close_time"])
                    )
                    if sig.close_time != expected_close:
                        item["action"] = "rejected_stale_signal"
                        item["expected_close_time"] = expected_close
                        symbol_result["signals"].append(item)
                        log.error(
                            "SIGNAL_REJECT_STALE symbol=%s event_id=%s tf=%s signal_close=%s expected_close=%s",
                            sig.symbol, sig.event_id, sig.timeframe, sig.close_time, expected_close,
                        )
                        continue

                    if not execute:
                        item["action"] = "preview"
                        item["case_active"] = is_case_active(state, sig.symbol, sig.combo)
                        symbol_result["signals"].append(item)
                        continue

                    if is_case_active(state, sig.symbol, sig.combo):
                        item["action"] = "active_case_suppressed"
                        item["reason"] = "FINAL14 one-position-per-combo"
                        symbol_result["signals"].append(item)
                        log.info(
                            "FINAL14_ACTIVE_SUPPRESS symbol=%s combo=%s event_id=%s",
                            sig.symbol, sig.combo, sig.event_id,
                        )
                        continue

                    targets = executor.targets()
                    results = []

                    # Engine stays LIVE, but unsafe execution conditions block
                    # only the order leg rather than stopping the service.
                    execution_block_reason = None
                    if state.backend != "postgres":
                        execution_block_reason = "persistent_postgres_unavailable"
                    elif not targets:
                        execution_block_reason = "bingx_trade_credentials_unavailable"

                    if execution_block_reason:
                        log.error(
                            "ORDER_EXECUTION_BLOCKED symbol=%s event_id=%s reason=%s",
                            sig.symbol, sig.event_id, execution_block_reason,
                        )
                        blocked = {
                            "target": "bingx_account",
                            "ok": False,
                            "processed": False,
                            "stage": "execution_blocked",
                            "error": execution_block_reason,
                        }
                        item["execution"] = [blocked]
                        item["action"] = "execution_blocked"
                        item["execution_block_reason"] = execution_block_reason
                        item["discord_error"] = executor.send_execution_error_discord(sig, blocked)
                        symbol_result["signals"].append(item)
                        continue

                    for target_name, url, amount in targets:
                        state_target = f"{target_name}:{'dryrun' if settings.dry_run else 'live'}"
                        if state.seen(sig.event_id, state_target):
                            results.append({
                                "target": target_name,
                                "action": "duplicate_ignored",
                                "ok": True,
                                "processed": True,
                            })
                            continue

                        result = executor.send_target(sig, target_name, url, amount)
                        results.append(result)

                        # IMPORTANT: once BingX has accepted the entry, this
                        # signal must never create a second MARKET entry even
                        # if TP/SL/trailing or Discord later fails.
                        if result.get("processed"):
                            state.mark(
                                sig.event_id,
                                state_target,
                                json.dumps(result, ensure_ascii=False),
                            )

                        if result.get("ok") and result.get("entry_filled"):
                            register_execution(state, sig, result)

                        if result.get("ok"):
                            discord_state_key = f"discord:{sig.symbol}"
                            if state.seen(sig.event_id, discord_state_key):
                                item["discord"] = {
                                    "target": discord_state_key,
                                    "action": "duplicate_ignored",
                                    "ok": True,
                                }
                            else:
                                discord_result = executor.send_execution_discord(sig, result)
                                item["discord"] = discord_result
                                if discord_result.get("ok"):
                                    state.mark(
                                        sig.event_id,
                                        discord_state_key,
                                        json.dumps(discord_result, ensure_ascii=False),
                                    )
                        else:
                            error_state_key = f"discord:error:{target_name}"
                            if state.seen(sig.event_id, error_state_key):
                                item["discord_error"] = {
                                    "target": error_state_key,
                                    "action": "duplicate_ignored",
                                    "ok": True,
                                }
                            else:
                                error_result = executor.send_execution_error_discord(sig, result)
                                item["discord_error"] = error_result
                                if error_result.get("ok"):
                                    state.mark(
                                        sig.event_id,
                                        error_state_key,
                                        json.dumps(error_result, ensure_ascii=False),
                                    )

                            # If entry was filled but protection is incomplete,
                            # also send the trade channel the actual BingX state.
                            if result.get("entry_filled"):
                                trade_state_key = f"discord:{sig.symbol}"
                                if not state.seen(sig.event_id, trade_state_key):
                                    trade_result = executor.send_execution_discord(sig, result)
                                    item["discord"] = trade_result
                                    if trade_result.get("ok"):
                                        state.mark(
                                            sig.event_id,
                                            trade_state_key,
                                            json.dumps(trade_result, ensure_ascii=False),
                                        )

                    item["execution"] = results
                    if all(r.get("ok") for r in results):
                        item["action"] = "sent"
                    elif any(r.get("processed") for r in results):
                        item["action"] = "processed_with_error"
                    else:
                        item["action"] = "failed_before_entry"
                    symbol_result["signals"].append(item)
                summary["symbols"][symbol] = symbol_result
                log.info(
                    "SYMBOL_SCAN_DONE symbol=%s signals=%s elapsed_ms=%s",
                    symbol, len(signals), round((time.monotonic() - symbol_started) * 1000, 1),
                )
            except Exception as exc:
                elapsed_ms = round((time.monotonic() - symbol_started) * 1000, 1)
                if isinstance(exc, BingXApiError):
                    log.error(
                        "SYMBOL_SCAN_BINGX_ERROR symbol=%s elapsed_ms=%s code=%s retry_at_ms=%s error=%s",
                        symbol, elapsed_ms, exc.code, exc.retry_at_ms, exc,
                    )
                else:
                    log.exception(
                        "SYMBOL_SCAN_ERROR symbol=%s elapsed_ms=%s error=%s",
                        symbol, elapsed_ms, exc,
                    )

                summary["status"] = "partial_error"
                summary["symbols"][symbol] = {"error": str(exc)}
                if isinstance(exc, BingXApiError):
                    blocked_until = max(
                        int(exc.retry_at_ms or 0),
                        int(market.blocked_until_ms),
                    )
                    if blocked_until > int(time.time() * 1000):
                        state.set_runtime_value(
                            BINGX_COOLDOWN_STATE_KEY,
                            str(blocked_until),
                        )
                        summary["retry_at_ms"] = blocked_until
                    if str(exc.code) in {"109415", "109425", "109429"}:
                        summary["stopped_early"] = True
                        summary["stop_reason"] = f"BingX {exc.code}; stopped to avoid further invalid requests"
                        break
        summary["elapsed_sec"] = round(time.time() - started, 3)
        last_scan_summary = summary
        return summary
    finally:
        scan_lock.release()


def _run_one_shot_btc_buy_test() -> None:
    """Place one BTC BUY live test at exactly 1 USDT margin x100.

    Idempotency is persisted before any order call. A restart therefore cannot
    create a second entry after an ambiguous network/process failure.
    """
    if not ONE_SHOT_BTC_BUY_TEST:
        return
    if state.backend != "postgres":
        log.error("ONE_SHOT_TEST_SKIPPED reason=postgres_required")
        return

    existing = state.get_runtime_value(ONE_SHOT_BTC_BUY_STATE_KEY, "")
    if existing:
        log.warning("ONE_SHOT_TEST_SKIPPED reason=already_attempted state=%s", existing[:500])
        return

    armed_at = int(time.time() * 1000)
    state.set_runtime_value(
        ONE_SHOT_BTC_BUY_STATE_KEY,
        json.dumps({"status": "armed", "armed_at": armed_at}),
    )

    symbol = "BTC-USDT"
    side = "BUY"
    test_settings = replace(settings, order_margin_usdt=1.0, leverage=100)
    test_executor = Executor(test_settings)

    try:
        cooldown_ms = max(
            0,
            _effective_bingx_blocked_until_ms() - int(time.time() * 1000),
        )
        if cooldown_ms > 0:
            wait_sec = min(cooldown_ms / 1000.0 + 1.0, 180.0)
            log.warning("ONE_SHOT_TEST_WAIT cooldown_ms=%s wait_sec=%.1f", cooldown_ms, wait_sec)
            time.sleep(wait_sec)

        candles = market.klines(symbol, "1m", 2)
        if candles.empty:
            raise RuntimeError("no 1m market data")
        entry = float(candles.iloc[-1]["close"])
        tp = entry * (1.0 + test_settings.tp_pct)
        sl = entry * (1.0 - test_settings.sl_pct)

        signal = Signal(
            symbol=symbol,
            combo=0,
            side=side,
            timeframe="TEST",
            close_time=int(time.time() * 1000),
            entry=entry,
            tp=tp,
            sl=sl,
            smc_dir=0,
        )

        log.warning(
            "ONE_SHOT_LIVE_TEST_START symbol=%s side=%s margin_usdt=1.0 leverage=100 ref_entry=%s",
            symbol, side, entry,
        )
        result = test_executor._execute_direct_bingx(signal)
        result["manual_test"] = True
        result["one_shot"] = True
        result["symbol"] = symbol
        result["side"] = side
        result["forced_margin_usdt"] = 1.0
        result["forced_leverage"] = 100

        # Always post the trade-channel summary so the user sees BingX state.
        summary_discord = test_executor.send_execution_discord(signal, result)
        raw_discord = test_executor.send_execution_raw_discord(signal, result)
        error_discord = None
        if not result.get("ok"):
            error_discord = test_executor.send_execution_error_discord(signal, result)

        persisted = {
            "status": "done",
            "finished_at": int(time.time() * 1000),
            "result": result,
            "discord_summary": summary_discord,
            "discord_raw": raw_discord,
            "discord_error": error_discord,
        }
        state.set_runtime_value(
            ONE_SHOT_BTC_BUY_STATE_KEY,
            json.dumps(persisted, ensure_ascii=False),
        )
        log.warning(
            "ONE_SHOT_LIVE_TEST_DONE ok=%s stage=%s order_id=%s sl_ok=%s tp_ok=%s trailing_ok=%s",
            result.get("ok"), result.get("stage"), result.get("order_id"),
            result.get("sl_ok"), result.get("tp_ok"), result.get("trailing_ok"),
        )
    except Exception as exc:
        failed = {
            "status": "failed",
            "finished_at": int(time.time() * 1000),
            "error": str(exc),
        }
        state.set_runtime_value(
            ONE_SHOT_BTC_BUY_STATE_KEY,
            json.dumps(failed, ensure_ascii=False),
        )
        log.exception("ONE_SHOT_LIVE_TEST_FAILED error=%s", exc)
        # Send operational failure to the error webhook without ever retrying the entry.
        try:
            test_signal = Signal(
                symbol=symbol,
                combo=0,
                side=side,
                timeframe="TEST",
                close_time=int(time.time() * 1000),
                entry=1.0,
                tp=2.0,
                sl=0.5,
                smc_dir=0,
            )
            test_executor.send_execution_error_discord(
                test_signal,
                {"stage": "one_shot_startup", "error": str(exc)},
            )
        except Exception:
            log.exception("ONE_SHOT_DISCORD_ERROR_FAILED")



def _run_one_shot_eth_buy_test() -> None:
    """Place one ETH BUY live test at exactly 1 USDT margin x100."""
    if not ONE_SHOT_ETH_BUY_TEST:
        return
    if state.backend != "postgres":
        log.error("ONE_SHOT_ETH_TEST_SKIPPED reason=postgres_required")
        return

    existing = state.get_runtime_value(ONE_SHOT_ETH_BUY_STATE_KEY, "")
    if existing:
        log.warning("ONE_SHOT_ETH_TEST_SKIPPED reason=already_attempted state=%s", existing[:500])
        return

    armed_at = int(time.time() * 1000)
    state.set_runtime_value(
        ONE_SHOT_ETH_BUY_STATE_KEY,
        json.dumps({"status": "armed", "armed_at": armed_at}),
    )

    symbol = "ETH-USDT"
    side = "BUY"
    test_settings = replace(settings, order_margin_usdt=1.0, leverage=100)
    test_executor = Executor(test_settings)

    try:
        cooldown_ms = max(
            0,
            _effective_bingx_blocked_until_ms() - int(time.time() * 1000),
        )
        if cooldown_ms > 0:
            wait_sec = min(cooldown_ms / 1000.0 + 1.0, 180.0)
            log.warning("ONE_SHOT_ETH_TEST_WAIT cooldown_ms=%s wait_sec=%.1f", cooldown_ms, wait_sec)
            time.sleep(wait_sec)

        candles = market.klines(symbol, "1m", 2)
        if candles.empty:
            raise RuntimeError("no 1m market data")
        entry = float(candles.iloc[-1]["close"])
        tp = entry * (1.0 + test_settings.tp_pct)
        sl = entry * (1.0 - test_settings.sl_pct)

        signal = Signal(
            symbol=symbol,
            combo=0,
            side=side,
            timeframe="TEST",
            close_time=int(time.time() * 1000),
            entry=entry,
            tp=tp,
            sl=sl,
            smc_dir=0,
        )

        log.warning(
            "ONE_SHOT_ETH_LIVE_TEST_START symbol=%s side=%s margin_usdt=1.0 leverage=100 ref_entry=%s",
            symbol, side, entry,
        )
        result = test_executor._execute_direct_bingx(signal)
        result["manual_test"] = True
        result["one_shot"] = True
        result["symbol"] = symbol
        result["side"] = side
        result["forced_margin_usdt"] = 1.0
        result["forced_leverage"] = 100

        summary_discord = test_executor.send_execution_discord(signal, result)
        raw_discord = test_executor.send_execution_raw_discord(signal, result)
        error_discord = None
        if not result.get("ok"):
            error_discord = test_executor.send_execution_error_discord(signal, result)

        persisted = {
            "status": "done",
            "finished_at": int(time.time() * 1000),
            "result": result,
            "discord_summary": summary_discord,
            "discord_raw": raw_discord,
            "discord_error": error_discord,
        }
        state.set_runtime_value(
            ONE_SHOT_ETH_BUY_STATE_KEY,
            json.dumps(persisted, ensure_ascii=False),
        )
        log.warning(
            "ONE_SHOT_ETH_LIVE_TEST_DONE ok=%s stage=%s order_id=%s sl_ok=%s tp_ok=%s trailing_ok=%s",
            result.get("ok"), result.get("stage"), result.get("order_id"),
            result.get("sl_ok"), result.get("tp_ok"), result.get("trailing_ok"),
        )
    except Exception as exc:
        failed = {
            "status": "failed",
            "finished_at": int(time.time() * 1000),
            "error": str(exc),
        }
        state.set_runtime_value(
            ONE_SHOT_ETH_BUY_STATE_KEY,
            json.dumps(failed, ensure_ascii=False),
        )
        log.exception("ONE_SHOT_ETH_LIVE_TEST_FAILED error=%s", exc)
        try:
            test_signal = Signal(
                symbol=symbol,
                combo=0,
                side=side,
                timeframe="TEST",
                close_time=int(time.time() * 1000),
                entry=1.0,
                tp=2.0,
                sl=0.5,
                smc_dir=0,
            )
            test_executor.send_execution_error_discord(
                test_signal,
                {"stage": "one_shot_eth_startup", "error": str(exc)},
            )
        except Exception:
            log.exception("ONE_SHOT_ETH_DISCORD_ERROR_FAILED")


def _run_one_shot_close_test(symbol: str, state_key: str) -> None:
    """Cancel protection orders, close LONG by market, then verify LONG=0."""
    if state.backend != "postgres":
        log.error("ONE_SHOT_CLOSE_SKIPPED symbol=%s reason=postgres_required", symbol)
        return

    existing = state.get_runtime_value(state_key, "")
    if existing:
        log.warning("ONE_SHOT_CLOSE_SKIPPED symbol=%s reason=already_attempted state=%s", symbol, existing[:500])
        return

    state.set_runtime_value(
        state_key,
        json.dumps({"status": "armed", "symbol": symbol, "armed_at": int(time.time() * 1000)}),
    )

    try:
        log.warning("ONE_SHOT_CLOSE_START symbol=%s", symbol)
        result = executor.emergency_close_long(symbol)
        discord_result = executor.send_emergency_close_discord(symbol, result)
        persisted = {
            "status": "done",
            "finished_at": int(time.time() * 1000),
            "result": result,
            "discord": discord_result,
        }
        state.set_runtime_value(state_key, json.dumps(persisted, ensure_ascii=False))
        log.warning(
            "ONE_SHOT_CLOSE_DONE symbol=%s ok=%s stage=%s requested_qty=%s remaining_long=%s",
            symbol, result.get("ok"), result.get("stage"),
            result.get("requested_close_qty"), result.get("remaining_long_qty"),
        )
    except Exception as exc:
        failed = {
            "status": "failed",
            "finished_at": int(time.time() * 1000),
            "error": str(exc),
        }
        state.set_runtime_value(state_key, json.dumps(failed, ensure_ascii=False))
        log.exception("ONE_SHOT_CLOSE_FAILED symbol=%s error=%s", symbol, exc)
        try:
            executor._post_discord(
                executor.discord_url(symbol),
                f"🚨 **EMERGENCY CLOSE TEST FAILED | {symbol}**\nError: `{str(exc)[:1200]}`",
                f"discord:close:{symbol}",
            )
        except Exception:
            log.exception("ONE_SHOT_CLOSE_DISCORD_FAILED symbol=%s", symbol)

def scheduled_scan():
    log.info(
        "SCHEDULE_TICK dry_run=%s symbols=%s market_mode=15m_only_incremental",
        settings.dry_run, list(settings.symbols),
    )
    result = run_scan(execute=True)
    log.info(
        "SCHEDULE_DONE status=%s elapsed_sec=%s summary=%s",
        result.get("status"), result.get("elapsed_sec"),
        json.dumps(result, ensure_ascii=False)[:3000],
    )

    discord_alert = send_discord_scan_alert(settings, result)
    log.info(
        "DISCORD_SCAN_ALERT ok=%s sent=%s status_code=%s reason=%s error=%s",
        discord_alert.get("ok"),
        discord_alert.get("sent"),
        discord_alert.get("status_code"),
        discord_alert.get("reason"),
        discord_alert.get("error"),
    )

    discord_report = send_discord_scan_summary(settings, result)
    log.info(
        "DISCORD_15M_REPORT ok=%s sent=%s status_code=%s reason=%s error=%s",
        discord_report.get("ok"),
        discord_report.get("sent"),
        discord_report.get("status_code"),
        discord_report.get("reason"),
        discord_report.get("error"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler

    # Install ERROR-only Discord forwarding before validation so startup
    # configuration/runtime errors are visible without killing the service.
    discord_log = install_discord_log_handler(settings)
    if discord_log.get("ok"):
        log.info(
            "DISCORD_LOG_FORWARD installed=%s level=%s reason=%s",
            discord_log.get("installed"), discord_log.get("level"), discord_log.get("reason"),
        )
    else:
        log.error(
            "DISCORD_LOG_FORWARD installed=false reason=%s",
            discord_log.get("reason"),
        )

    persisted_cooldown = _effective_bingx_blocked_until_ms()
    if persisted_cooldown > int(time.time() * 1000):
        log.warning(
            "BINGX_COOLDOWN_RESTORED retry_at_ms=%s remaining_sec=%s",
            persisted_cooldown,
            int((persisted_cooldown - int(time.time() * 1000) + 999) / 1000),
        )

    errors, warnings = validate_settings(settings)
    log.info("STARTUP_CONFIG %s", json.dumps(safe_config_snapshot(settings), ensure_ascii=False))
    log.info("STRATEGY_PROFILE %s", json.dumps(strategy_static_snapshot(), ensure_ascii=False))

    for warning in warnings:
        log.warning("CONFIG_WARNING %s", warning)

    if errors:
        for error in errors:
            log.error("CONFIG_ERROR %s", error)
        log.error(
            "CONFIG_VALIDATION ok=false errors=%s warnings=%s service_continues=true",
            len(errors), len(warnings),
        )
    else:
        log.info("CONFIG_VALIDATION ok=true warnings=%s", len(warnings))
    log.info(
        "STATE_BACKEND backend=%s persistent=%s",
        state.backend,
        state.backend == "postgres",
    )

    self_test_result = run_startup_self_test(settings, state)
    for item in self_test_result.get("checks", []):
        level = log.info if item.get("ok") else log.error
        level(
            "STARTUP_TEST name=%s ok=%s elapsed_ms=%s details=%s error=%s",
            item.get("name"),
            item.get("ok"),
            item.get("elapsed_ms"),
            json.dumps(item.get("details", {}), ensure_ascii=False)[:1000],
            item.get("error"),
        )
    if not self_test_result.get("ok"):
        log.error(
            "STARTUP_TEST_SUMMARY ok=false checks=%s service_continues=true order_execution_guarded=true",
            len(self_test_result.get("checks", [])),
        )
    else:
        log.info("STARTUP_TEST_SUMMARY ok=true checks=%s network_calls=0 order_calls=0 mode=startup_light", len(self_test_result.get("checks", [])))

    discord_test = send_discord_startup_test(settings)
    if discord_test.get("ok"):
        log.info(
            "DISCORD_STARTUP_TEST ok=true sent=%s status_code=%s",
            discord_test.get("sent"), discord_test.get("status_code"),
        )
    else:
        log.warning(
            "DISCORD_STARTUP_TEST ok=false reason=%s error=%s status_code=%s body=%s",
            discord_test.get("reason"),
            discord_test.get("error"),
            discord_test.get("status_code"),
            discord_test.get("body"),
        )

    if ONE_SHOT_BTC_BUY_TEST:
        threading.Thread(
            target=_run_one_shot_btc_buy_test,
            name="one-shot-btc-buy-test",
            daemon=True,
        ).start()
        log.warning("ONE_SHOT_TEST_THREAD_STARTED symbol=BTC-USDT side=BUY margin_usdt=1.0 leverage=100")

    if ONE_SHOT_ETH_BUY_TEST:
        threading.Thread(
            target=_run_one_shot_eth_buy_test,
            name="one-shot-eth-buy-test",
            daemon=True,
        ).start()
        log.warning("ONE_SHOT_ETH_TEST_THREAD_STARTED symbol=ETH-USDT side=BUY margin_usdt=1.0 leverage=100")

    if ONE_SHOT_BTC_CLOSE_TEST:
        threading.Thread(
            target=_run_one_shot_close_test,
            args=("BTC-USDT", ONE_SHOT_BTC_CLOSE_STATE_KEY),
            name="one-shot-btc-close-test",
            daemon=True,
        ).start()
        log.warning("ONE_SHOT_CLOSE_THREAD_STARTED symbol=BTC-USDT")

    if ONE_SHOT_ETH_CLOSE_TEST:
        threading.Thread(
            target=_run_one_shot_close_test,
            args=("ETH-USDT", ONE_SHOT_ETH_CLOSE_STATE_KEY),
            name="one-shot-eth-close-test",
            daemon=True,
        ).start()
        log.warning("ONE_SHOT_CLOSE_THREAD_STARTED symbol=ETH-USDT")

    if settings.auto_scheduler and not MANUAL_ORDER_TEST_MODE:
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(
            scheduled_scan,
            CronTrigger(minute="0,15,30,45", second=settings.scheduler_second, timezone="UTC"),
            id="scan_15m",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )
        scheduler.start()
        log.info("Scheduler enabled at minutes 00/15/30/45 + %ss UTC", settings.scheduler_second)
    else:
        log.info("Scheduler disabled; manual_order_test_mode=%s", MANUAL_ORDER_TEST_MODE)
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="BingX FINAL14 RR Hard-TP Autotrade Engine", version="14.0.0", lifespan=lifespan)


@app.get("/health")
def health():
    profile = strategy_static_snapshot()
    return {
        "ok": True,
        "engine": "BingX FINAL14 RR Hard-TP Autotrade Engine",
        "engine_version": "14.0.0",
        "strategy_version": profile.get("version"),
        "strategy_source_run_id": profile.get("source_run_id"),
        "final14_enabled": profile.get("enabled"),
        "exit_mode": profile.get("exit"),
        "layer2_source": "locked_per_case_FINAL14_config",
        "dry_run": settings.dry_run,
        "symbols": settings.symbols,
        "smc_mode": settings.smc_mode,
        "scheduler": bool(settings.auto_scheduler and not MANUAL_ORDER_TEST_MODE),
        "manual_order_test_mode": MANUAL_ORDER_TEST_MODE,
        "one_shot_btc_buy_test": ONE_SHOT_BTC_BUY_TEST,
        "one_shot_btc_buy_state": state.get_runtime_value(ONE_SHOT_BTC_BUY_STATE_KEY, ""),
        "one_shot_eth_buy_test": ONE_SHOT_ETH_BUY_TEST,
        "one_shot_eth_buy_state": state.get_runtime_value(ONE_SHOT_ETH_BUY_STATE_KEY, ""),
        "one_shot_btc_close_test": ONE_SHOT_BTC_CLOSE_TEST,
        "one_shot_btc_close_state": state.get_runtime_value(ONE_SHOT_BTC_CLOSE_STATE_KEY, ""),
        "one_shot_eth_close_test": ONE_SHOT_ETH_CLOSE_TEST,
        "one_shot_eth_close_state": state.get_runtime_value(ONE_SHOT_ETH_CLOSE_STATE_KEY, ""),
        "market_mode": "15m_only_incremental",
        "state_backend": state.backend,
        "execution_ready": state.backend == "postgres" and bool(executor.targets()),
        "execution_mode": "direct_bingx_single_account_fixed_margin",
        "order_target_count": len(executor.targets()),
        "live_limit_15m": settings.live_limit_15m,
        "bingx_blocked_until_ms": _effective_bingx_blocked_until_ms(),
        "bingx_cooldown_remaining_ms": max(
            0, _effective_bingx_blocked_until_ms() - int(time.time() * 1000)
        ),
    }



def _require_scan_token(x_scan_token: str | None) -> None:
    # Live scheduler never uses these routes. Manual API-triggered market
    # requests are disabled unless a secret token is explicitly configured.
    if not settings.scan_token:
        raise HTTPException(status_code=403, detail="manual market requests disabled")
    if x_scan_token != settings.scan_token:
        raise HTTPException(status_code=403, detail="invalid scan token")


@app.get("/market-check")
def market_check(x_scan_token: str | None = Header(default=None)):
    _require_scan_token(x_scan_token)
    try:
        symbols = market.contract_symbols()
        configured = list(settings.symbols)
        return {
            "ok": True,
            "configured_symbols": configured,
            "supported": {s: s in symbols for s in configured},
            "contract_count": len(symbols),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
        }



@app.get("/kline-check")
def kline_check(symbol: str = "BTC-USDT", interval: str = "15m", limit: int = 3, x_scan_token: str | None = Header(default=None)):
    _require_scan_token(x_scan_token)
    """Single Kline request with timestamp diagnostics; never places orders."""
    try:
        now_ms = int(time.time() * 1000)
        df = market.klines(symbol.upper(), interval, limit)
        rows = []
        for _, row in df.tail(3).iterrows():
            rows.append({
                "open_time": int(row["open_time"]),
                "close_time": int(row["close_time"]),
                "close": float(row["close"]),
                "close_minus_now_ms": int(row["close_time"]) - now_ms,
            })
        closed = closed_only(df, now_ms)
        result = {
            "ok": True,
            "symbol": symbol.upper(),
            "interval": interval,
            "requested_limit": limit,
            "now_ms": now_ms,
            "received_candles": len(df),
            "closed_candles": len(closed),
            "rows": rows,
        }
        if len(closed):
            last = closed.iloc[-1]
            result["last_closed"] = {
                "close": float(last["close"]),
                "open_time": int(last["open_time"]),
                "close_time": int(last["close_time"]),
            }
        return result
    except Exception as exc:
        return {
            "ok": False,
            "symbol": symbol.upper(),
            "interval": interval,
            "requested_limit": limit,
            "error": str(exc),
        }


@app.get("/manual-test-preflight")
def manual_test_preflight(
    symbol: str = "BTC-USDT",
    side: str = "BUY",
    x_scan_token: str | None = Header(default=None),
):
    """Validate one tiny live test order without placing it."""
    _require_scan_token(x_scan_token)
    symbol = symbol.upper().strip()
    side = side.upper().strip()

    if settings.auto_scheduler and not MANUAL_ORDER_TEST_MODE:
        raise HTTPException(
            status_code=409,
            detail="manual live test requires scheduler disabled",
        )
    if symbol not in {"BTC-USDT", "ETH-USDT"}:
        raise HTTPException(status_code=400, detail="test symbol must be BTC-USDT or ETH-USDT")
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")

    try:
        candles = market.klines(symbol, "1m", 2)
        if candles.empty:
            raise RuntimeError("no 1m market data")
        entry = float(candles.iloc[-1]["close"])

        executor._assert_hedge_mode()
        sizing = executor._prepare_fixed_size(symbol, side, entry)

        return {
            "ok": True,
            "order_will_be_placed": False,
            "symbol": symbol,
            "side": side,
            "reference_price": entry,
            "margin_usdt": settings.order_margin_usdt,
            "leverage": settings.leverage,
            "target_notional_usdt": settings.order_margin_usdt * settings.leverage,
            "quantity": sizing["qty"],
            "half_quantity": sizing["half_qty"],
            "tp_quantity": sizing["tp_qty"],
            "trailing_quantity": sizing["trailing_qty"],
            "exit_quantity_total": sizing["tp_qty"] + sizing["trailing_qty"],
            "quantity_precision": sizing["quantity_precision"],
            "price_precision": sizing["price_precision"],
            "min_qty": sizing["min_qty"],
            "min_usdt": sizing["min_usdt"],
            "hedge_mode": True,
            "scheduler": settings.auto_scheduler,
            "protection_mode": "legacy_separate_orders",
        }
    except Exception as exc:
        log.warning("MANUAL_TEST_PREFLIGHT_FAILED symbol=%s side=%s error=%s", symbol, side, exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/manual-order-test")
def manual_order_test(
    symbol: str = "BTC-USDT",
    side: str = "BUY",
    x_scan_token: str | None = Header(default=None),
):
    """Place exactly one tiny live test trade using the production executor."""
    _require_scan_token(x_scan_token)
    symbol = symbol.upper().strip()
    side = side.upper().strip()

    if settings.auto_scheduler and not MANUAL_ORDER_TEST_MODE:
        raise HTTPException(
            status_code=409,
            detail="manual live test requires scheduler disabled",
        )
    if symbol not in {"BTC-USDT", "ETH-USDT"}:
        raise HTTPException(status_code=400, detail="test symbol must be BTC-USDT or ETH-USDT")
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")

    try:
        candles = market.klines(symbol, "1m", 2)
        if candles.empty:
            raise RuntimeError("no 1m market data")
        entry = float(candles.iloc[-1]["close"])

        if side == "BUY":
            tp = entry * (1.0 + settings.tp_pct)
            sl = entry * (1.0 - settings.sl_pct)
        else:
            tp = entry * (1.0 - settings.tp_pct)
            sl = entry * (1.0 + settings.sl_pct)

        signal = Signal(
            symbol=symbol,
            combo=0,
            side=side,
            timeframe="TEST",
            close_time=int(time.time() * 1000),
            entry=entry,
            tp=tp,
            sl=sl,
            smc_dir=0,
        )

        log.warning(
            "MANUAL_LIVE_TEST_START symbol=%s side=%s margin_usdt=%s leverage=%s ref_entry=%s",
            symbol, side, settings.order_margin_usdt, settings.leverage, entry,
        )
        result = executor._execute_direct_bingx(signal)
        result["manual_test"] = True
        result["symbol"] = symbol
        result["side"] = side
        log.warning(
            "MANUAL_LIVE_TEST_DONE symbol=%s side=%s ok=%s stage=%s order_id=%s sl_ok=%s tp_ok=%s trailing_ok=%s",
            symbol, side, result.get("ok"), result.get("stage"), result.get("order_id"),
            result.get("sl_ok"), result.get("tp_ok"), result.get("trailing_ok"),
        )

        if result.get("ok"):
            executor.send_execution_discord(signal, result)
        else:
            executor.send_execution_error_discord(signal, result)

        return result
    except Exception as exc:
        log.exception("MANUAL_LIVE_TEST_FAILED symbol=%s side=%s", symbol, side)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/self-test")
def self_test(x_scan_token: str | None = Header(default=None)):
    _require_scan_token(x_scan_token)
    result = run_self_test(settings)
    log.info("SELF_TEST_MANUAL ok=%s checks=%s", result.get("ok"), len(result.get("checks", [])))
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result)
    return result


@app.post("/discord-test")
def discord_test(x_scan_token: str | None = Header(default=None)):
    _require_scan_token(x_scan_token)
    result = send_discord_startup_test(settings)
    log.info(
        "DISCORD_MANUAL_TEST ok=%s sent=%s status_code=%s",
        result.get("ok"), result.get("sent"), result.get("status_code"),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    return result


@app.get("/status")
def status():
    return last_scan_summary


@app.post("/preview")
def preview(x_scan_token: str | None = Header(default=None)):
    _require_scan_token(x_scan_token)
    result = run_scan(execute=False)
    if result.get("status") == "busy":
        raise HTTPException(status_code=409, detail=result)
    return result


@app.post("/scan")
def scan(x_scan_token: str | None = Header(default=None)):
    _require_scan_token(x_scan_token)
    result = run_scan(execute=True)
    if result.get("status") == "busy":
        raise HTTPException(status_code=409, detail=result)
    return result
