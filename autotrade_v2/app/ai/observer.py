"""Independent, asynchronous Gemini observer. NEVER participates in execution decisions.

The signal-time candle snapshot is frozen on submission. The worker owns its
database connections and network calls. Failure is recorded or logged, never
propagated into the 20FINAL trading pipeline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic, time, sleep

import pandas as pd
import requests

from .gemini_config import generation_config
from .external_research import BinanceFuturesPublic
from .bingx_feedback import load_bingx_feedback
from .bingx_post_learning import (eligible, evidence_payload, evidence_hash,
                                 prompt_for_review, parse_post_response,
                                 MODEL_PROMPT_VERSION)
from ..strategy.final14_config import get_case
from ..strategy.final14_research.smc_ob import OBConfig, ob_context
from ..strategy.final14_research.smc_structure import smc_direction

LOG = logging.getLogger(__name__)
PROMPT_VERSION = "observe.pre.research.v3"
POST_PROMPT_VERSION = "observe.post.research.v3"
RULES_VERSION = "observe.only.v1"
VALID_DECISIONS = {"ALLOW", "BLOCK", "UNSURE"}


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _candles_at_signal(frame: pd.DataFrame, close_ms: int) -> pd.DataFrame:
    """Strictly exclude any incomplete/future 15m candle, even on delayed WS delivery."""
    required = {"open_time", "close_time", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        raise ValueError("snapshot_columns_missing")
    d = frame.loc[
        (frame["close_time"].astype("int64") <= int(close_ms))
        & (frame["open_time"].astype("int64") < int(close_ms))
    ].copy()
    d = d.sort_values("open_time").drop_duplicates("open_time", keep="last")
    return d.tail(1600).reset_index(drop=True)


def _resample_complete(d: pd.DataFrame, hours: int) -> pd.DataFrame:
    """Only full 1H/4H bars formed by exactly contiguous closed 15m candles."""
    if d.empty:
        return d
    expected = hours * 4
    t = d.copy()
    t.index = pd.to_datetime(t["open_time"], unit="ms", utc=True)
    records = []
    for bucket, b in t.resample(f"{hours}h", label="left", closed="left"):
        opens = b["open_time"].astype("int64").tolist()
        bucket_ms = int(bucket.timestamp() * 1000)
        if (
            len(opens) != expected
            or opens != [bucket_ms + x * 900_000 for x in range(expected)]
        ):
            continue
        records.append({
            "open_time": bucket_ms, "close_time": int(b["close_time"].iloc[-1]),
            "open": float(b["open"].iloc[0]), "high": float(b["high"].max()),
            "low": float(b["low"].min()), "close": float(b["close"].iloc[-1]),
            "volume": float(b["volume"].sum()),
        })
    return pd.DataFrame.from_records(records, columns=d.columns.tolist())


def _smc_features(d: pd.DataFrame, name: str) -> list[dict]:
    if len(d) < 65:
        return []
    features = []
    trend = int(smc_direction(d, swing_len=50).iloc[-1])
    features.append({
        "id": f"{name}-SMC", "tf": name, "kind": "SMC_DIRECTION",
        "value": {1: "BULLISH", -1: "BEARISH", 0: "MIXED"}[trend],
        "source": "smc_structure.smc_direction",
    })
    ob = ob_context(d, OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5)).iloc[-1]
    features.append({
        "id": f"{name}-OB", "tf": name, "kind": "ORDER_BLOCK_CONTEXT",
        "buy_blocked": bool(ob["buy_blocked"]), "sell_blocked": bool(ob["sell_blocked"]),
        "bull_ob_low": float(ob["bull_ob_low"]) if pd.notna(ob["bull_ob_low"]) else None,
        "bull_ob_high": float(ob["bull_ob_high"]) if pd.notna(ob["bull_ob_high"]) else None,
        "bear_ob_low": float(ob["bear_ob_low"]) if pd.notna(ob["bear_ob_low"]) else None,
        "bear_ob_high": float(ob["bear_ob_high"]) if pd.notna(ob["bear_ob_high"]) else None,
        "source": "smc_ob.ob_context",
    })
    return features


def build_pre_context(intent, raw: pd.DataFrame) -> dict:
    d15 = _candles_at_signal(raw, intent.close_time)
    frames = {"15m": d15, "1h": _resample_complete(d15, 1),
              "4h": _resample_complete(d15, 4)}
    features = []
    candles = {}
    for name, d in frames.items():
        features.extend(_smc_features(d, name))
        columns = ("open_time", "open", "high", "low", "close", "volume", "close_time")
        candles[name] = [
            {k: (int(getattr(row, k)) if k.endswith("time") else float(getattr(row, k)))
             for k in columns}
            for row in d.tail({"15m": 60, "1h": 48, "4h": 48}[name]).itertuples(index=False)
        ]
    return {
        "contract_version": "observe.pre.v1",
        "as_of_ms": int(intent.close_time),
        "signal": asdict(intent),
        "features": features,
        "candles": candles,
        # Explicitly unavailable until timestamp-verified news feed is implemented.
        "news": [], "news_status": "UNAVAILABLE",
        "guardrails": {"mode": "OBSERVE", "ai_cannot_affect_trade": True},
    }


def _candidate_output(response: dict) -> dict:
    candidate = (response.get("candidates") or [{}])[0]
    parts = ((candidate.get("content") or {}).get("parts") or [])
    raw = "".join(part.get("text", "") for part in parts if "text" in part)
    output = json.loads(raw)
    if not isinstance(output, dict) or output.get("decision") not in VALID_DECISIONS:
        raise ValueError("invalid_ai_decision")
    flags = output.get("risk_flags", [])
    if not isinstance(flags, list):
        raise ValueError("invalid_risk_flags")
    return output


def _validated_output(out: dict, ctx: dict) -> dict:
    ids = {feature["id"] for feature in ctx["features"]}
    flags = out.get("risk_flags") or []
    if any(not isinstance(f, dict) or not isinstance(f.get("evidence_ids"), list)
           or any(e not in ids for e in f["evidence_ids"]) for f in flags):
        raise ValueError("unverified_evidence")
    for field in ("smc_alignment", "news_risk"):
        if field in out and not isinstance(out[field], str):
            raise ValueError("invalid_" + field)
    # Model's self-confidence is NOT an empirically calibrated probability.
    conf = out.get("confidence")
    if conf is not None and (not isinstance(conf, (float, int)) or isinstance(conf, bool)
                              or not 0 <= conf <= 1):
        raise ValueError("invalid_confidence")
    return out


class GeminiClient:
    def __init__(self, key: str, model: str, timeout_sec: float = 25.0, transport=None):
        self.key = key
        self.model = model
        self.timeout_sec = timeout_sec
        self.transport = transport or requests.post

    def research_news(self, symbol: str, signal_close_ms: int) -> dict:
        """Google Search grounding with source URLs, never pre-signal verified news."""
        observed_ms = int(time() * 1000)
        message = (
            f"Tìm nguồn tin tài chính mới về {symbol}, crypto, Fed, CPI, ETF. "
            "Viết tối đa 4 câu tiếng Việt, chỉ đưa sự kiện có nguồn. "
            "Không có tin kiểm chứng thì nói rõ. Thời điểm tín hiệu (milliseconds UTC) "
            f"{signal_close_ms}. Đây là nghiên cứu SAU tín hiệu. Không được khẳng định "
            "một tin đã tồn tại trước tín hiệu nếu không có published_ms xác minh."
        )
        response = self.transport(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": message}]}],
                  "tools": [{"google_search": {}}],
                  "generationConfig": generation_config(self.model, 1024)},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        candidate = (response.json().get("candidates") or [{}])[0]
        grounding = candidate.get("groundingMetadata") or {}
        refs = []
        for item in grounding.get("groundingChunks") or []:
            web = item.get("web") or {}
            uri = web.get("uri")
            if isinstance(uri, str) and uri.startswith("https://"):
                refs.append({"url": uri[:700], "title": str(web.get("title") or "")[:140],
                             "published_ms": None})
            if len(refs) == 5:
                break
        summary = "".join(p.get("text", "") for p in
                          (candidate.get("content") or {}).get("parts", [])
                          if isinstance(p, dict))
        if not refs:
            return {"status": "UNAVAILABLE", "observed_ms": observed_ms,
                    "sources": [], "summary": ""}
        return {"status": "GROUNDED_REVIEW_TIME", "observed_ms": observed_ms,
                "observed_after_signal": observed_ms > int(signal_close_ms),
                "publication_time_verified": False, "sources": refs,
                "summary": summary[:1600]}

    def ask(self, prompt: str) -> tuple[dict, dict, int]:
        started = monotonic()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        for attempt in range(2):
            response = self.transport(
                url, headers={"x-goog-api-key": self.key, "Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": generation_config(
                          self.model, 3072, "application/json")},
                timeout=self.timeout_sec,
            )
            if response.status_code != 503 or attempt == 1:
                break
            # Google explicitly documents short backoff for temporary 503s.
            # This worker is independent of the 20FINAL executor.
            sleep(0.75)
        response.raise_for_status()
        data = response.json()
        return data, data.get("usageMetadata") or {}, int((monotonic() - started) * 1000)


def _pre_prompt(ctx: dict) -> str:
    return (
        "You are a read-only, independent trading auditor. The 20FINAL strategy already "
        "made its decision, and you CANNOT change or delay the real order. "
        "Analyze SMC only from the INPUT's closed candles and verified features. "
        "Do not repeat the existing combo SMC veto as a new finding. "
        "BOS, CHoCH, FVG, liquidity not explicitly computed are interpretations, "
        "never verified features. Cite feature ids in risk_flags evidence_ids. "
        "Use independent Binance SMC (independent_smc) to compare against bot SMC. "
        "Prior lessons, if supplied, were saved BEFORE the signal timestamp; "
        "they are hypotheses for scrutiny, never training weights or proven edges. "
        "External SMC must be based on independently fetched closed Binance candles "
        "as of the actual signal close; describe BOS, CHoCH and FVG levels precisely "
        "and distinguish algorithmic heuristic findings from AI opinions. "
        "Never invent liquidity zones: label estimated pools as hypotheses. "
        "Respect actual per-combo strategy_config: for example, a 4h OB cannot "
        "be characterized as the mandatory filter for a 1h-only OB strategy. "
        "Historical_derivatives has source timestamps <= signal close; "
        "recognize publication lag as unknown and do NOT pretend this data "
        "was necessarily available in real time at Entry. "
        "Live funding, open interest and mark prices captured AFTER the signal "
        "are research-time context ONLY, not verified entry-time conditions. "
        "External_news is grounded at RESEARCH time, not necessarily signal time; "
        "without trustworthy published_ms <= signal close, news_risk MUST be UNKNOWN. "
        "Do not invent headlines, liquidation events or unproven causal relationships. "
        "Return ONLY JSON object with decision ALLOW, BLOCK or UNSURE, confidence 0..1, "
        "smc_alignment ALIGNED, MIXED, CONFLICT or UNKNOWN, news_risk UNKNOWN, "
        "risk_flags as array of {code,severity,evidence_ids}, "
        "reasoning_verified and reasoning_ai_opinion as distinct strings, watch_points array. "
        "BLOCK is a hypothetical assessment, not an instruction to execute. "
        "Write all human-readable commentary in natural Vietnamese (vi-VN) with accents: "
        "reasoning_verified, reasoning_ai_opinion, watch_points, and any explanatory "
        "text in risk_flags. Preserve JSON keys, enum values, risk codes, severity, "
        "feature IDs, evidence_ids, and all numeric values exactly; never translate "
        "machine-readable identifiers. Do not convert hypothetical BLOCK into a trade veto. "
        "INPUT:\n" + _json(ctx)
    )


def _post_prompt(ctx: dict) -> str:
    return (
        "You are an independent post-trade analyst. A CLOSED intent was confirmed by "
        "the reconciliation ledger. Never assume outcome from merged BingX symbol positions. "
        "Compare immutable signal-time AI review with verified results; avoid hindsight bias. "
        "Do not invent SMC events, money values, exit motives, fees or news. "
        "Use external_exit_window only as Binance historical price context, "
        "never as verified BingX entry/exit prices or TP/SL evidence. "
        "Do not infer the cause of wins or losses from correlation alone, "
        "and never retroactively treat research-time news as pre-signal knowledge. "
        "If evidence inadequate choose INCONCLUSIVE. Return ONLY a JSON object with keys "
        "primary_cause, contributing_factors (array), pre_trade_assessment "
        "(CORRECT|INCORRECT|INCONCLUSIVE), explanation, lessons (array). "
        "Write ALL human-readable analysis in natural Vietnamese (vi-VN) with accents, "
        "including primary_cause, contributing_factors, explanation, and every lesson. "
        "Preserve JSON keys, CORRECT/INCORRECT/INCONCLUSIVE values, identifiers, "
        "trade numbers and source evidence unchanged; do not translate machine-readable codes. "
        "INPUT:\n" + _json(ctx)
    )


class GeminiObserver:
    """Bounded non-blocking queue; restart safe via immutable event_id inserts."""

    def __init__(self, database_url: str, *, enabled: bool | None = None,
                 key: str | None = None, model: str | None = None,
                 client: GeminiClient | None = None, poll_seconds: int = 60,
                 event_cb=None):
        self.database_url = database_url
        self.event_cb = event_cb
        self.enabled = (os.getenv("AI_OBSERVE_ENABLED", "false").lower() in ("1", "true", "yes")
                        if enabled is None else enabled)
        api_key = os.getenv("GEMINI_API_KEY", "") if key is None else key
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        self.client = client or (GeminiClient(api_key, self.model) if api_key else None)
        self._stop = Event()
        self._thread = None
        self._queue: Queue = Queue(maxsize=32)
        self._daily_lock = Lock()
        self._daily = ("", 0)
        self._news_daily = ("", 0)
        self.market_enabled = os.getenv("AI_EXTERNAL_MARKET_ENABLED", "false").lower() in ("1", "true", "yes")
        self.news_enabled = os.getenv("AI_NEWS_GROUNDING_ENABLED", "false").lower() in ("1", "true", "yes")
        self.max_news_per_day = min(30, max(0, int(os.getenv("AI_NEWS_MAX_CALLS_PER_DAY", "8"))))
        self.market = BinanceFuturesPublic()
        self.max_calls_per_day = min(500, max(0, int(os.getenv("AI_DAILY_MAX_CALLS", "50"))))
        self.poll_seconds = poll_seconds
        self._last_post_check = 0.0
        self.bingx_post_enabled = os.getenv("AI_BINGX_POST_ENABLED", "false").lower() in ("1", "true", "yes")
        self._bingx_post_thread = None
        self._no_result_table_logged = False

    def start(self):
        if not (self.enabled and self.client and self.database_url):
            if self.enabled:
                LOG.warning("AI_OBSERVER_INACTIVE reason=missing_key_or_database")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="v2-ai-observer", daemon=True)
        self._thread.start()
        if self.bingx_post_enabled:
            self._bingx_post_thread = Thread(target=self._bingx_post_loop,
                                             name="v2-ai-bingx-post", daemon=True)
            self._bingx_post_thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if self._bingx_post_thread:
            self._bingx_post_thread.join(timeout=1)

    def submit(self, intents, snapshot) -> bool:
        """Called before executor; a full queue never delays or rejects trading."""
        if not self.enabled or not self._thread or not self._thread.is_alive() or not intents:
            return False
        try:
            # Copy raw closed-candle data before executor runs. AI never reads
            # live exchange state or mutable snapshots for this prediction.
            frozen = snapshot.candles.tail(1600).copy(deep=True)
            self._queue.put_nowait((tuple(intents), frozen))
            return True
        except Full:
            LOG.warning("AI_OBSERVER_DROPPED reason=queue_full")
            return False
        except Exception as exc:
            LOG.warning("AI_OBSERVER_SUBMIT_FAIL error_class=%s", type(exc).__name__)
            return False

    def _announce(self, key: str, event_id: str, symbol: str,
                  combo: int | None, details: dict):
        # Reuse the existing asynchronous Layer-4 EventReporter/Discord routes.
        # Never make Gemini notifications part of any trade decision.
        if self.event_cb is None:
            return
        try:
            self.event_cb(key, "INFO", "Gemini analysis recorded",
                          event_id=event_id, symbol=symbol,
                          combo=combo, details=details)
        except Exception as exc:
            LOG.warning("AI_DISCORD_ANNOUNCE_FAIL event_id=%s error_class=%s",
                        event_id, type(exc).__name__)

    def _connection(self):
        import psycopg
        return psycopg.connect(self.database_url, connect_timeout=3,
                               prepare_threshold=None)

    def _try_budget(self):
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._daily_lock:
            d, n = self._daily
            if day != d:
                d, n = day, 0
            if n >= self.max_calls_per_day:
                self._daily = (d, n)
                return False
            self._daily = (d, n + 1)
            return True

    def _try_news_budget(self):
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._daily_lock:
            d, count = self._news_daily
            if d != day:
                d, count = day, 0
            if count >= self.max_news_per_day:
                self._news_daily = (d, count)
                return False
            self._news_daily = (d, count + 1)
            return True

    def _external_pre(self, intent, ctx):
        """Enrichment in observer daemon only, never in the executor path."""
        cfg = get_case(intent.symbol, intent.combo)
        ctx["strategy_config"] = {
            "combo_config": dict(cfg) if cfg else None,
            "note": "Locked thresholds; no independent recalculation of all entry indicators.",
        }
        market, structure = {"status": "DISABLED"}, {"status": "DISABLED"}
        if self.market_enabled:
            try:
                market = self.market.capture(intent.symbol, intent.close_time)
            except Exception as exc:
                LOG.warning("AI_EXTERNAL_MARKET_FAIL error_class=%s", type(exc).__name__)
                market = {"status": "UNAVAILABLE"}
            try:
                structure = self.market.structure_as_of(intent.symbol, intent.close_time)
            except Exception as exc:
                LOG.warning("AI_INDEPENDENT_SMC_FAIL error_class=%s", type(exc).__name__)
                structure = {"status": "UNAVAILABLE"}
        ctx["external_market"] = market
        historical = {"status": "DISABLED"}
        if self.market_enabled:
            try:
                historical = self.market.derivatives_as_of(intent.symbol, intent.close_time)
            except Exception as exc:
                LOG.warning("AI_HISTORICAL_DERIVATIVES_FAIL error_class=%s",
                            type(exc).__name__)
                historical = {"status": "UNAVAILABLE"}
        ctx["historical_derivatives"] = historical
        for kind, datum in (
            ("FUNDING", historical.get("funding")),
            ("OI", historical.get("open_interest")),
        ):
            if datum:
                ctx["features"].append({
                    "id": f"EXT-BINANCE-{kind}-ASOF",
                    "kind": f"HISTORICAL_{kind}_ASOF",
                    "source": "Binance historical public REST",
                    "as_of_ms": intent.close_time,
                    "publication_lag_unknown": True,
                    "data": datum,
                })
        ctx["independent_smc"] = structure
        for tf, evidence in structure.get("timeframes", {}).items():
            ctx["features"].append({
                "id": f"EXT-BINANCE-SMC-{tf}", "kind": "INDEPENDENT_SMC_HEURISTIC",
                "source": "Binance USD-M historical klines",
                "as_of_ms": intent.close_time, "data": evidence,
            })
        if market.get("mark"):
            ctx["features"].append({
                "id": "EXT-BINANCE-MARK", "kind": "RESEARCH_TIME_MARK",
                "data": market["mark"], "observed_after_signal": True,
            })
        if market.get("open_interest"):
            ctx["features"].append({
                "id": "EXT-BINANCE-OI", "kind": "RESEARCH_TIME_OPEN_INTEREST",
                "data": market["open_interest"], "observed_after_signal": True,
            })
        news = {"status": "UNAVAILABLE", "sources": []}
        if (self.news_enabled and self.client is not None
                and callable(getattr(self.client, "research_news", None))
                and self._try_news_budget() and self._try_budget()):
            try:
                news = self.client.research_news(intent.symbol, intent.close_time)
            except Exception as exc:
                LOG.warning("AI_NEWS_GROUNDING_FAIL error_class=%s", type(exc).__name__)
        ctx["external_news"] = news
        ctx["news_status"] = news.get("status", "UNAVAILABLE")
        if news.get("status") == "GROUNDED_REVIEW_TIME":
            for i, source in enumerate(news.get("sources", [])[:5]):
                ctx["features"].append({
                    "id": f"EXT-NEWS-{i+1}", "kind": "RESEARCH_TIME_NEWS",
                    "url": source.get("url"), "published_ms": source.get("published_ms"),
                    "observed_after_signal": True,
                })

    def _pre_exists(self, event_id):
        with self._connection() as con:
            return con.execute(
                "SELECT 1 FROM autotrade_analytics.ai_reviews WHERE event_id=%s",
                (event_id,)
            ).fetchone() is not None

    def _write_pre(self, intent, ctx, decision, output, latency_ms,
                   usage, error_code=None):
        from psycopg.types.json import Jsonb
        with self._connection() as con:
            inserted = con.execute("""
                INSERT INTO autotrade_analytics.ai_reviews (
                    event_id,symbol,combo,side,timeframe,signal_close_ms,
                    mode,ai_decision,final_action,confidence,smc_alignment,news_risk,
                    news_status,risk_flags,input_json,input_hash,output_json,
                    model,prompt_version,rules_version,latency_ms,tokens_in,tokens_out,error_code
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,'OBSERVE',%s,'NO_INTERVENTION',%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                ) ON CONFLICT (event_id) DO NOTHING RETURNING event_id
            """, (
                intent.event_id,intent.symbol,
                "TIER" if intent.combo == 11 else f"C{intent.combo-90 if intent.combo >= 101 else intent.combo}",
                intent.side,intent.timeframe,int(intent.close_time),decision,
                output.get("confidence") if output else None,
                output.get("smc_alignment") if output else None,
                output.get("news_risk", "UNKNOWN") if output else "UNKNOWN",
                ctx.get("news_status", "UNAVAILABLE"),
                Jsonb(output.get("risk_flags", [])) if output else Jsonb([]),
                Jsonb(ctx),_digest(ctx),Jsonb(output) if output else None,
                self.model,PROMPT_VERSION,RULES_VERSION,latency_ms,
                usage.get("promptTokenCount"),usage.get("candidatesTokenCount"),error_code,
            ))
            return inserted.fetchone() is not None

    def _observe_one(self, intent, raw):
        try:
            if self._pre_exists(intent.event_id):
                return
            ctx = build_pre_context(intent, raw)
            decision, output, elapsed, usage, error = "ERROR", None, 0, {}, None
            if self._try_budget():
                started = monotonic()
                try:
                    if self.bingx_post_enabled:
                        ctx["prior_lessons"] = self._prior_lessons(intent)
                    self._external_pre(intent, ctx)
                    response, usage, elapsed = self.client.ask(_pre_prompt(ctx))
                    output = _validated_output(_candidate_output(response), ctx)
                    decision = output["decision"]
                except requests.Timeout:
                    decision, error = "TIMEOUT", "gemini_timeout"
                except (ValueError, TypeError, KeyError, IndexError):
                    decision, error = "INVALID", "invalid_gemini_output"
                except Exception as exc:
                    decision, error = "ERROR", type(exc).__name__
                elapsed = max(elapsed, int((monotonic() - started) * 1000))
            else:
                error = "daily_call_cap"
            inserted = self._write_pre(intent, ctx, decision, output, elapsed, usage, error)
            if inserted and output is not None and decision in VALID_DECISIONS:
                self._announce("L4.AI.PRE_REVIEW", intent.event_id, intent.symbol,
                               int(intent.combo), {
                    "side": intent.side, "ai_decision": decision,
                    "smc_alignment": output.get("smc_alignment"),
                    "news_status": ctx.get("news_status", "UNAVAILABLE"),
                    "news_risk": output.get("news_risk"),
                    "independent_smc_status": ctx.get("independent_smc", {}).get("status"),
                    "external_market_status": ctx.get("external_market", {}).get("status"),
                    "news_source_count": len(ctx.get("external_news", {}).get("sources", [])),
                    "independent_smc_summary": " | ".join(
                        f"{tf}: {data.get('trend', 'UNKNOWN')}"
                        for tf, data in ctx.get("independent_smc", {}).get(
                            "timeframes", {}).items()
                    ),
                    "news_sources": ctx.get("external_news", {}).get("sources", [])[:2],
                    "reasoning_verified": output.get("reasoning_verified"),
                    "reasoning_ai_opinion": output.get("reasoning_ai_opinion"),
                    "entry": intent.entry, "tp": intent.tp, "sl": intent.sl,
                })
        except Exception as exc:
            LOG.warning("AI_PRE_REVIEW_FAIL event_id=%s error_class=%s",
                        intent.event_id, type(exc).__name__)

    def _pending_closed(self):
        with self._connection() as con:
            exists = con.execute(
                "SELECT to_regclass('autotrade_analytics.trade_results')"
            ).fetchone()[0]
            if exists is None:
                if not self._no_result_table_logged:
                    LOG.info("AI_POST_DISABLED reason=verified_trade_results_unavailable")
                    self._no_result_table_logged = True
                return []
            return con.execute("""
                SELECT r.event_id,r.revision,r.payload,a.input_json,a.output_json
                  FROM autotrade_analytics.trade_results r
                  JOIN autotrade_analytics.ai_reviews a ON a.event_id=r.event_id
                 WHERE r.status='CLOSED'
                   AND r.revision > 0
                   AND NOT EXISTS (
                       SELECT 1 FROM autotrade_analytics.ai_post_reviews p
                       WHERE p.event_id=r.event_id AND p.trade_result_revision=r.revision
                   )
                 ORDER BY r.closed_ms LIMIT 3
            """).fetchall()

    def _write_post(self, event_id, revision, payload, ctx, output, latency_ms,
                    usage, error_code):
        from psycopg.types.json import Jsonb
        with self._connection() as con:
            inserted = con.execute("""
                INSERT INTO autotrade_analytics.ai_post_reviews(
                    event_id,trade_result_revision,entry_order_id,exit_order_ids,
                    outcome,pnl,fees,funding,roi,primary_cause,pre_trade_assessment,
                    output_json,input_hash,model,prompt_version,latency_ms,
                    tokens_in,tokens_out,error_code
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                ) ON CONFLICT (event_id,trade_result_revision) DO NOTHING RETURNING event_id
            """, (
                event_id,revision,payload.get("entry_order_id"),
                Jsonb(payload.get("exit_order_ids") or []),payload.get("outcome"),
                payload.get("pnl"),payload.get("fees"),payload.get("funding"),
                payload.get("roi"),output.get("primary_cause") if output else None,
                output.get("pre_trade_assessment") if output else None,
                Jsonb(output) if output else None,_digest(ctx),self.model,
                POST_PROMPT_VERSION,latency_ms,usage.get("promptTokenCount"),
                usage.get("candidatesTokenCount"),error_code,
            ))
            return inserted.fetchone() is not None

    def _observe_closed(self):
        try:
            closed = self._pending_closed()
        except Exception as exc:
            LOG.warning("AI_POST_SCAN_FAIL error_class=%s", type(exc).__name__)
            return
        for event_id,revision,payload,pre,review in closed:
            try:
                if not isinstance(payload, dict) or payload.get("status") != "CLOSED":
                    continue
                ctx = {"contract_version": "observe.post.v1", "event_id": event_id,
                       "revision": int(revision), "pre": pre, "pre_review": review,
                       "verified_result": payload}
                if self.market_enabled:
                    try:
                        orig = pre.get("signal") if isinstance(pre, dict) else {}
                        ctx["external_exit_window"] = self.market.exit_window(
                            orig.get("symbol") or payload.get("symbol"),
                            orig.get("close_time"),
                            payload.get("closed_ms"),
                        )
                    except Exception as exc:
                        LOG.warning("AI_EXTERNAL_EXIT_FAIL error_class=%s", type(exc).__name__)
                        ctx["external_exit_window"] = {"status": "UNAVAILABLE"}
                if not self._try_budget():
                    return
                started = monotonic()
                result,usage,elapsed,error = None,{},0,None
                try:
                    response,usage,elapsed = self.client.ask(_post_prompt(ctx))
                    candidate = (response.get("candidates") or [{}])[0]
                    raw = "".join(x.get("text","") for x in
                                  (candidate.get("content") or {}).get("parts",[]))
                    result = json.loads(raw)
                    if not isinstance(result,dict) or result.get("pre_trade_assessment") not in (
                        "CORRECT","INCORRECT","INCONCLUSIVE"
                    ):
                        raise ValueError("invalid_post_response")
                except Exception as exc:
                    error = type(exc).__name__
                elapsed = max(elapsed,int((monotonic()-started)*1000))
                inserted = self._write_post(event_id,revision,payload,ctx,result,elapsed,usage,error)
                if inserted and result:
                    signal = pre.get("signal") if isinstance(pre, dict) else {}
                    signal = signal if isinstance(signal, dict) else {}
                    combo = signal.get("combo")
                    self._announce("L4.AI.POST_REVIEW", event_id,
                                   payload.get("symbol") or signal.get("symbol") or "",
                                   int(combo) if combo is not None else None, {
                        "combo": payload.get("combo"),
                        "side": payload.get("side") or signal.get("side"),
                        "outcome": payload.get("outcome"),
                        "pnl": payload.get("pnl"),
                        "roi": payload.get("roi"),
                        "revision": revision,
                        "pre_decision": review.get("decision") if isinstance(review, dict) else None,
                        "pre_trade_assessment": result.get("pre_trade_assessment"),
                        "explanation": result.get("explanation"),
                        "lessons": result.get("lessons"),
                        "external_exit_window": ctx.get("external_exit_window"),
                    })
            except Exception as exc:
                LOG.warning("AI_POST_REVIEW_FAIL event_id=%s error_class=%s",
                            event_id,type(exc).__name__)


    def _prior_lessons(self, intent):
        """No look-ahead: a past lesson must already be written at signal time."""
        label = ("TIER" if intent.combo == 11 else
                 f"C{intent.combo-90 if intent.combo >= 101 else intent.combo}")
        try:
            with self._connection() as con:
                exists = con.execute(
                    "SELECT to_regclass('autotrade_analytics.ai_bingx_post_reviews')"
                ).fetchone()[0]
                if exists is None:
                    return []
                rows = con.execute("""
                    SELECT assessment,analysis_json->'lessons',evidence_quality
                      FROM autotrade_analytics.ai_bingx_post_reviews
                     WHERE state='READY' AND symbol=%s AND combo=%s
                       AND closed_ms < %s
                       AND completed_at <= to_timestamp(%s / 1000.0)
                     ORDER BY completed_at DESC LIMIT 3
                """, (intent.symbol, label, int(intent.close_time),
                      int(intent.close_time))).fetchall()
            return [{"assessment": item[0], "lessons": item[1],
                     "quality": item[2]} for item in rows]
        except Exception as exc:
            LOG.warning("AI_PRIOR_LESSONS_READ_FAIL error_class=%s", type(exc).__name__)
            return []

    def _bingx_candidates(self):
        """Read Dashboard's materialized BingX data; no runtime exchange call."""
        from psycopg.rows import dict_row
        with self._connection() as con:
            if con.execute(
                "SELECT to_regclass('autotrade_analytics.ai_bingx_post_reviews')"
            ).fetchone()[0] is None:
                return []
            with con.cursor(row_factory=dict_row) as cur:
                reviews = cur.execute("""
                    SELECT event_id,symbol,combo,side,signal_close_ms,ai_decision,
                           input_json,output_json
                      FROM autotrade_analytics.ai_reviews
                     WHERE ai_decision IN ('ALLOW','BLOCK')
                     ORDER BY signal_close_ms DESC LIMIT 100
                """).fetchall()
                if not reviews:
                    return []
                with con.transaction():
                    evidence = load_bingx_feedback(cur, reviews)
        return [(review, evidence[review["event_id"]])
                for review in reviews
                if eligible(review, evidence.get(review["event_id"]))]

    def _claim_bingx(self, review, evidence, fingerprint):
        from psycopg.types.json import Jsonb
        with self._connection() as con:
            row = con.execute("""
                INSERT INTO autotrade_analytics.ai_bingx_post_reviews (
                    event_id,evidence_hash,entry_order_id,symbol,combo,closed_ms,
                    evidence_quality,outcome,pnl,roi,evidence_json,ai_decision,
                    state,attempts,started_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING',1,now())
                ON CONFLICT (event_id,evidence_hash) DO UPDATE
                    SET state='PENDING',
                        attempts=autotrade_analytics.ai_bingx_post_reviews.attempts + 1,
                        started_at=now(),error_code=NULL
                WHERE autotrade_analytics.ai_bingx_post_reviews.attempts < 3
                  AND (
                      (autotrade_analytics.ai_bingx_post_reviews.state='FAILED'
                       AND started_at < now()-interval '30 minutes')
                      OR
                      (autotrade_analytics.ai_bingx_post_reviews.state='PENDING'
                       AND started_at < now()-interval '10 minutes')
                  )
                RETURNING attempts
            """, (
                review["event_id"], fingerprint, evidence["entry_order_id"],
                review["symbol"], review["combo"], evidence["closed_ms"],
                evidence["quality"], evidence["outcome"], evidence.get("pnl"),
                evidence.get("roi"), Jsonb(evidence), review["ai_decision"],
            )).fetchone()
            return int(row[0]) if row else None

    def _finish_bingx(self, event_id, fingerprint, attempt, result, error):
        from psycopg.types.json import Jsonb
        with self._connection() as con:
            row = con.execute("""
                UPDATE autotrade_analytics.ai_bingx_post_reviews
                   SET state=%s, completed_at=now(), assessment=%s,
                       analysis_json=%s, model=%s,prompt_version=%s,error_code=%s
                 WHERE event_id=%s AND evidence_hash=%s
                   AND state='PENDING' AND attempts=%s
                RETURNING event_id
            """, (
                "READY" if result else "FAILED",
                result.get("pre_trade_assessment") if result else None,
                Jsonb(result) if result else None, self.model,
                MODEL_PROMPT_VERSION, error, event_id, fingerprint, attempt,
            )).fetchone()
            return row is not None

    def _observe_bingx_closed(self):
        try:
            candidates = self._bingx_candidates()
        except Exception as exc:
            LOG.warning("AI_BINGX_POST_SCAN_FAIL error_class=%s", type(exc).__name__)
            return
        reviewed_this_cycle = 0
        for review, evidence in candidates:
            if self._stop.is_set() or reviewed_this_cycle >= 3:
                break
            payload = evidence_payload(review, evidence)
            fingerprint = evidence_hash(payload)
            if not self._try_budget():
                return
            try:
                attempt = self._claim_bingx(review, payload, fingerprint)
            except Exception as exc:
                LOG.warning("AI_BINGX_POST_CLAIM_FAIL error_class=%s", type(exc).__name__)
                return
            if attempt is None:
                continue
            reviewed_this_cycle += 1
            result, error = None, None
            try:
                response, _, _ = self.client.ask(prompt_for_review(review, payload))
                result = parse_post_response(response, payload)
            except Exception as exc:
                error = type(exc).__name__[:80]
            try:
                inserted = self._finish_bingx(review["event_id"], fingerprint,
                                              attempt, result, error)
                if inserted and result:
                    self._announce("L4.AI.BINGX_POST_REVIEW", review["event_id"],
                                   review["symbol"], None, {
                        "combo": review["combo"], "side": review["side"],
                        "outcome": payload["outcome"], "pnl": payload["pnl"],
                        "roi": payload["roi"], "evidence_quality": payload["quality"],
                        "pre_decision": review["ai_decision"],
                        "pre_trade_assessment": result["pre_trade_assessment"],
                        "explanation": result["explanation"],
                        "lessons": result["lessons"],
                        "evidence_hash": fingerprint[:12],
                    })
            except Exception as exc:
                LOG.warning("AI_BINGX_POST_WRITE_FAIL event_id=%s error_class=%s",
                            review["event_id"], type(exc).__name__)

    def _bingx_post_loop(self):
        while not self._stop.is_set():
            self._observe_bingx_closed()
            self._stop.wait(max(180, self.poll_seconds))

    def _run(self):
        while not self._stop.is_set():
            try:
                intents, frozen = self._queue.get(timeout=2)
            except Empty:
                intents = ()
                frozen = None
            for intent in intents:
                if self._stop.is_set():
                    return
                self._observe_one(intent, frozen)
            now = monotonic()
            if now - self._last_post_check >= self.poll_seconds:
                self._last_post_check = now
                self._observe_closed()
