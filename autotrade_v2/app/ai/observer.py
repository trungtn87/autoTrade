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
from time import monotonic, time

import pandas as pd
import requests

from ..strategy.final14_research.smc_ob import OBConfig, ob_context
from ..strategy.final14_research.smc_structure import smc_direction

LOG = logging.getLogger(__name__)
PROMPT_VERSION = "observe.pre.v1"
POST_PROMPT_VERSION = "observe.post.v1"
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
    def __init__(self, key: str, model: str, timeout_sec: float = 12.0, transport=None):
        self.key = key
        self.model = model
        self.timeout_sec = timeout_sec
        self.transport = transport or requests.post

    def ask(self, prompt: str) -> tuple[dict, dict, int]:
        started = monotonic()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        response = self.transport(
            url, headers={"x-goog-api-key": self.key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"responseMimeType": "application/json",
                                       "temperature": 0, "maxOutputTokens": 1400}},
            timeout=self.timeout_sec,
        )
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
        "News is UNAVAILABLE: news_risk must be UNKNOWN and never invent headlines. "
        "Return ONLY JSON object with decision ALLOW, BLOCK or UNSURE, confidence 0..1, "
        "smc_alignment ALIGNED, MIXED, CONFLICT or UNKNOWN, news_risk UNKNOWN, "
        "risk_flags as array of {code,severity,evidence_ids}, "
        "reasoning_verified and reasoning_ai_opinion as distinct strings, watch_points array. "
        "BLOCK is a hypothetical assessment, not an instruction to execute. "
        "INPUT:\n" + _json(ctx)
    )


def _post_prompt(ctx: dict) -> str:
    return (
        "You are an independent post-trade analyst. A CLOSED intent was confirmed by "
        "the reconciliation ledger. Never assume outcome from merged BingX symbol positions. "
        "Compare immutable signal-time AI review with verified results; avoid hindsight bias. "
        "Do not invent SMC events, money values, exit motives, fees or news. "
        "If evidence inadequate choose INCONCLUSIVE. Return ONLY a JSON object with keys "
        "primary_cause, contributing_factors (array), pre_trade_assessment "
        "(CORRECT|INCORRECT|INCONCLUSIVE), explanation, lessons (array). "
        "INPUT:\n" + _json(ctx)
    )


class GeminiObserver:
    """Bounded non-blocking queue; restart safe via immutable event_id inserts."""

    def __init__(self, database_url: str, *, enabled: bool | None = None,
                 key: str | None = None, model: str | None = None,
                 client: GeminiClient | None = None, poll_seconds: int = 60):
        self.database_url = database_url
        self.enabled = (os.getenv("AI_OBSERVE_ENABLED", "false").lower() in ("1", "true", "yes")
                        if enabled is None else enabled)
        api_key = os.getenv("GEMINI_API_KEY", "") if key is None else key
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.client = client or (GeminiClient(api_key, self.model) if api_key else None)
        self._stop = Event()
        self._thread = None
        self._queue: Queue = Queue(maxsize=32)
        self._daily_lock = Lock()
        self._daily = ("", 0)
        self.max_calls_per_day = min(500, max(0, int(os.getenv("AI_DAILY_MAX_CALLS", "50"))))
        self.poll_seconds = poll_seconds
        self._last_post_check = 0.0
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

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

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
            con.execute("""
                INSERT INTO autotrade_analytics.ai_reviews (
                    event_id,symbol,combo,side,timeframe,signal_close_ms,
                    mode,ai_decision,final_action,confidence,smc_alignment,news_risk,
                    news_status,risk_flags,input_json,input_hash,output_json,
                    model,prompt_version,rules_version,latency_ms,tokens_in,tokens_out,error_code
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,'OBSERVE',%s,'NO_INTERVENTION',%s,%s,%s,
                    'UNAVAILABLE',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                ) ON CONFLICT (event_id) DO NOTHING
            """, (
                intent.event_id,intent.symbol,
                "TIER" if intent.combo == 11 else f"C{intent.combo-90 if intent.combo >= 101 else intent.combo}",
                intent.side,intent.timeframe,int(intent.close_time),decision,
                output.get("confidence") if output else None,
                output.get("smc_alignment") if output else None,
                output.get("news_risk", "UNKNOWN") if output else "UNKNOWN",
                Jsonb(output.get("risk_flags", [])) if output else Jsonb([]),
                Jsonb(ctx),_digest(ctx),Jsonb(output) if output else None,
                self.model,PROMPT_VERSION,RULES_VERSION,latency_ms,
                usage.get("promptTokenCount"),usage.get("candidatesTokenCount"),error_code,
            ))

    def _observe_one(self, intent, raw):
        try:
            if self._pre_exists(intent.event_id):
                return
            ctx = build_pre_context(intent, raw)
            decision, output, elapsed, usage, error = "ERROR", None, 0, {}, None
            if self._try_budget():
                started = monotonic()
                try:
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
            self._write_pre(intent, ctx, decision, output, elapsed, usage, error)
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
            con.execute("""
                INSERT INTO autotrade_analytics.ai_post_reviews(
                    event_id,trade_result_revision,entry_order_id,exit_order_ids,
                    outcome,pnl,fees,funding,roi,primary_cause,pre_trade_assessment,
                    output_json,input_hash,model,prompt_version,latency_ms,
                    tokens_in,tokens_out,error_code
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                ) ON CONFLICT (event_id,trade_result_revision) DO NOTHING
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
                self._write_post(event_id,revision,payload,ctx,result,elapsed,usage,error)
            except Exception as exc:
                LOG.warning("AI_POST_REVIEW_FAIL event_id=%s error_class=%s",
                            event_id,type(exc).__name__)

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
