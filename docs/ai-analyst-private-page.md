# AutoTrade AI Analyst private page

The unified AutoTrade service serves an isolated private /ai page for Gemini
trade analysis and persistent conversations. The existing /mobile Dashboard,
BingX engine, PWA, and Discord are unchanged.

## Features

- Password login using a dedicated VPS secret.
- Read immutable pre-trade SMC reviews from Supabase.
- View post-trade reviews only after individual CLOSED results are verified.
- Select one trade to ask follow-up questions, or compare available combos.
- Persist conversation threads and messages into private PostgreSQL tables.
- Gemini receives bounded read-only context. It has no trading tools or keys.
- Password rotation invalidates sessions. Cookies are HttpOnly, Secure, SameSite
  Strict, valid for 12 hours; all chat writes require signed-session CSRF.
- Max five bad login attempts per client in 15 minutes and default 30 chat
  requests/day, capped transactionally with idempotent request UUIDs.

## VPS configuration (same AutoTrade container)

AI_ANALYST_PASSWORD=<choose a strong unique password, at least 12 characters>
GEMINI_API_KEY=<existing Gemini secret>
AI_CHAT_DAILY_MAX_CALLS=30
GEMINI_MODEL=gemini-3.8-flash

AI_OBSERVE_ENABLED=true is a separate switch for automatic Gemini analysis.
The /ai page can work with AI observer off if prior reviews exist. Without an
API key, saved history remains readable but new chat is unavailable.

Open https://YOUR-AUTOTRADE-HOST/ai after setting the environment and
rebuilding/restarting the running application. Never put secrets into GitHub.

## Database rollout

Apply the additive migration file
supabase/migrations/20261108214500_ai_analyst_private_chat.sql. It creates
autotrade_analytics.ai_chat_sessions and ai_chat_messages, enables RLS and
revokes anonymous/authenticated grants. No trading or Dashboard data changes.

## Data limits / known gaps

The page defaults to the latest 30 pre-trade reviews. Summary counters are
aggregated across the private AI reviews. Chat context uses at most 10 recent
signals, the specifically selected review, bounded candles/features and 12
recent dialogue turns; messages are displayed as literal text, never HTML.

Trade results are not guessed from price action. The production verified
trade_results lifecycle table is still missing, so Post-Trade reviews cannot
be considered operational until the separate position-ledger safety work is
finished. Historical data may initially be empty until the observer generates
and stores its first pre-trade review.

This page is private even when DASHBOARD_PUBLIC_READONLY=true. No additional
BingX API, automated order decisions, or Dashboard UI changes are introduced.
