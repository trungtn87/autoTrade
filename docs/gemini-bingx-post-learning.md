# Gemini auto post-EXIT learning from BingX database

This phase **extends PR #87**; it does not change trading execution.

## What runs

1. 20FINAL continues to execute every signal without waiting for Gemini.
2. A separate daemon thread (enabled only by `AI_BINGX_POST_ENABLED=true`
   together with existing `AI_OBSERVE_ENABLED=true`) polls a maximum of 100
   recent ALLOW/BLOCK signal reviews every >=180 seconds, never using live BingX APIs.
3. Exact `event_id` to `signal_links.entry_order_id` mapping is required.
   The Dashboard's unchanged `load_results()` supplies closed results and
   the provenance label.
4. If the result is `CLOSED` or `ESTIMATED_CLOSED`, has a confirmed entry ID,
   a positive close time after the signal and WIN/LOSS/BREAKEVEN, a private
   `ai_bingx_post_reviews` record is claimed using a deterministic evidence
   hash. PENDING/FAILED claims can be reclaimed only after a cooldown and
   at most three attempts; exact duplicates never trigger another Gemini call.
5. Gemini receives the immutable original pre-trade review, relevant signal-time
   features, and the **BingX database** outcome; returns a bounded Vietnamese
   explanation and <=3 lessons. The worker enforces a deterministic
   `pre_trade_assessment` regardless of the model's requested label.
6. On success, only the new row is marked READY, a plainly labelled BTC/ETH
   Discord INFO summary is sent through existing Layer-4 routing **only
   when Exit is no more than 12 hours old** (historical backfills stay silent),
   and the private `/ai` page shows lessons if the evidence hash still matches.
   Prior lessons may inform *future* Gemini shadow reviews, but only if
   `completed_at <= signal_close_ms` — no hindsight from future data.

## Integrity

- `BINGX_VERIFIED_EXIT`: the outcome can be compared directionally. That
  is not proof of AI predictive advantage.
- `BINGX_VERIFIED_EXIT_ESTIMATED_MONEY` and
  `MERGED_POSITION_ESTIMATE`: **INCONCLUSIVE** is mandatory even if
  the AI claims confidence or if PnL/ROI estimate has a sign.
- `CLOSED_PENDING`, `UNRESOLVED`, missing/multiple entry links, manually
  imported history and Gemini timeout reviews: no automatic post-analysis.
- The original `ai_reviews` and ledger-only `ai_post_reviews` stay
  immutable and unchanged.
- The new private table is separate from `ai_post_reviews` so estimated
  results are never forged as `trade_results` revisions.
- Evidence corrections generate a **new** immutable hash/version; older
  revisions remain visible in the DB for audits.
- A failed optional BingX source never changes executor flow or hides
  earlier ai_reviews/AI chat.
- No automatic prompt rewriting, model fine-tuning or trading gate.
- All network calls to Gemini are in a separate daemon thread, with
  existing `AI_DAILY_MAX_CALLS` quota. No new API endpoint or BingX keys.

## Activation order

1. Merge after production CI, PostgreSQL integration and FINAL14 parity;
   apply `supabase/migrations/20261109160000_ai_bingx_post_learning.sql`
   to the **existing** AutoTrade Supabase project using approved workflow.
2. Redeploy `main`, set `AI_BINGX_POST_ENABLED=true` on VPS if desired.
   The feature stays OFF by default, including if the new table already exists.
3. Check private `/ai`, then inspect `ai_bingx_post_reviews`: READY means
   a stored analysis, PENDING a claim, FAILED a bounded provider error.
4. Assert the Discord text includes estimated provenance for merged positions.
   Do not confuse a manual position close with a verified per-combo EXIT.
5. To disable immediately, set `AI_BINGX_POST_ENABLED=false`; other observer
   workflows continue and the engine does not change.

## Tests

`autotrade_v2/tests/test_ai_bingx_post_learning.py`: missing entry, unresolved,
correctness guard for estimated money, hash corrections, duplicate claims,
Vietnamese prompt, provider validation and observer isolation.
`autotrade_v2/tests/test_ai_page.py`: private auth and history remain in place.
