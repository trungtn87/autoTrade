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
- AI_EXTERNAL_MARKET_ENABLED=true (opt-in after testing Binance connectivity from VPS)
- AI_NEWS_GROUNDING_ENABLED=true (opt-in, may incur Google Search grounding costs)
- AI_NEWS_MAX_CALLS_PER_DAY=8 (optional, max 30, also counts toward AI_DAILY_MAX_CALLS)

Do not configure AI_GATE_MODE or any policy to veto orders. There is **no**
GATE implementation in this phase. Disable with AI_OBSERVE_ENABLED=false.

Known limits:
- A hard daily number-of-calls cap, not price-exact dollar budget.
- Optional Google Search grounding retrieves URLs and a Vietnamese summary at **review time**.
  Search results normally do NOT have verified source publication milliseconds; they MUST
  NOT be treated as information known at Entry. No fabricated event citations.
- The public Binance USD-M futures market capture provides mark/funding/OI at
  **review time**, NOT as of Entry. For strictly pre-signal comparison, the
  observer separately downloads historical CLOSED 15m/1h/4h Binance candles
  with endTime <= signal_close_ms. The independent SMC heuristic computes
  confirmed swing highs/lows, close-break BOS/CHoCH, three-candle FVG and
  possible equal-high/low liquidity pools (hypotheses only). It does NOT reuse
  the engine's SMC calculator and cannot veto the engine.
- The post-trade observer may fetch Binance 1h historical price extrema for
  an exit window <=72 hours, but this is never proof of a BingX fill or exit.
  Old or unsupported windows remain UNAVAILABLE/OUT_OF_RANGE.
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

## Independent external research (phase 1 extension)

- Explicitly isolated from the signal and executor path. It executes only in
  the asynchronous observer daemon; disabling both new flags restores the
  previous input shape except for the locked combo configuration metadata.
- Binance APIs used: public `/fapi/v1/premiumIndex`, `/fapi/v1/openInterest`,
  `/fapi/v1/klines`. No BingX trading credentials or Binance API key needed.
  Every REST request has a 3-second timeout and fails open to UNAVAILABLE.
- A deterministic SMC algorithm creates auditable data independent of the
  strategy engine; Gemini must clearly distinguish this *heuristic* from an
  objectively validated institutional SMC event. When missing, it may only
  offer qualitative SMC interpretation as a hypothesis.
- The primary pre-review stays an immutable observation from the signal.
  Real-time Binance funding and OI / grounded news sampled later cannot be
  backdated. A source URL alone does NOT verify its publish time.
- Post-EXIT comparisons depend on a verified ledger CLOSED revision; only
  the original pre-review, ledger result and bounded historical Binance
  candles are considered. For positions >72h the external exit path is
  unavailable; ledger-based review remains possible.
- No AI model training, prompt self-update or shadow-trade execution is added.
- External integration is behind opt-in flags; do not enable on production
  without monitoring AI latency, Binance regional availability, Gemini quota,
  grounded-search billing and terms of use.
