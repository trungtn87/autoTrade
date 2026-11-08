# Gemini AI OBSERVE Phase 1

**Scope:** Independent shadow analysis only. TradeIntent generation and all
BingX entry, exit, TP/SL, trailing, cleanup and Force Close paths are unchanged.

## Data flow

- 20FINAL produces its intents normally.
- The orchestrator queues a deep copy of a fixed signal-time candle snapshot
  without waiting for a network or DB response; then it executes **every**
  intent exactly as it did before.
- A bounded daemon worker builds 15m/1h/4h SMC/OB context using only candles
  with close_time <= intent.close_time, calls Gemini, and inserts immutable
  ai_reviews rows with a hypothetical ALLOW/BLOCK/UNSURE decision.
- No observer output is ever consulted to decide whether to trade.
- A separate passive poll within the observer detects independently verified
  trade_results.status=CLOSED with revision>0 and adds immutable ai_post_reviews.
  This does not run until the lifecycle ledger exists and is populated.

## Production activation

Apply the additive SQL migration using the standard reviewed Supabase migration
workflow. The engine deploy is harmless without Gemini configuration:

- AI_OBSERVE_ENABLED=true
- GEMINI_API_KEY=<secret> (VPS environment only, never commit)
- GEMINI_MODEL=gemini-3.8-flash (optional)
- AI_DAILY_MAX_CALLS=50 (optional)

Do not configure AI_GATE_MODE or any policy to veto orders. There is **no**
GATE implementation in this phase. Disable with AI_OBSERVE_ENABLED=false.

Known limits:
- A hard daily number-of-calls cap, not price-exact dollar budget.
- News is explicitly UNAVAILABLE; no fabricated event citations.
  Future work: verified source/dated news cache + evidence policy.
- PostTrade depends on a proven writer for trade_results. The current
  production database does NOT have this table. NEVER enable the lifecycle
  writer until its collector compatibility issue is separately resolved and
  PostgreSQL integration tests pass. Trading is more important than analysis.
- Some reviews may be logged after an exchange order was sent, but their input
  snapshot is frozen at signal time and includes no later prices/fills.
- The AI rating is a hypothesis, not a calibrated probability or trade advice.
- For multi-entry merged BingX positions, any ambiguous or incomplete
  allocation must remain UNRESOLVED, never silently counted as CLOSED.

## Discord reporting (Dashboard unchanged)

Gemini does not create any new Dashboard screens and uses the **existing BTC/ETH
Discord channels** through Layer-4 EventReporter:

- New pre-trade review in Supabase -> Vietnamese report marked **hypothetical**
  ALLOW/BLOCK/UNSURE, with SMC reasoning and explicit non-intervention label.
- New post-trade review for a **verified CLOSED** combo -> Discord comparison
  against the original immutable pre-trade judgment, including PnL/ROI only
  when verified.
- Both reports are INFO (not alerts), routed by symbol; cannot place, block,
  modify or delay orders. Discord outage is not an execution dependency.
- Existing webhook and existing execution notification format remain unchanged.
- EventReporter INFO notifications are best-effort; persistent truth is the
  immutable Supabase AI review. No guarantee of replay after Discord outage.

**Activation:** AI_OBSERVE_ENABLED=true and GEMINI_API_KEY in VPS
environment. The deployed Gemini post-review worker remains inactive while
the verified trade_results table and lifecycle writer are absent; these are
separate safety prerequisites.
