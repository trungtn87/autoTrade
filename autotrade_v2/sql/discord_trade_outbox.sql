-- Trade notifications wait for final BingX outcome, then coalesce by candle close.
-- Store webhook routing only by symbol; never persist webhook secrets.
create table if not exists public.discord_trade_outbox (
    id bigint generated always as identity primary key,
    event_key text not null,
    event_id text not null,
    symbol text not null check (symbol in ('BTC-USDT', 'ETH-USDT')),
    batch_key text not null,
    payload jsonb not null,
    received_at timestamptz not null default now(),
    next_attempt_at timestamptz not null default now(),
    sent_at timestamptz,
    attempts integer not null default 0,
    last_error text,
    constraint discord_trade_outbox_unique_event unique (event_key, event_id)
);
create index if not exists discord_trade_outbox_unsent_idx
    on public.discord_trade_outbox (next_attempt_at,received_at)
    where sent_at is null;
create index if not exists discord_trade_outbox_batch_idx
    on public.discord_trade_outbox (batch_key,received_at)
    where sent_at is null;
alter table public.discord_trade_outbox enable row level security;
revoke all on public.discord_trade_outbox from anon, authenticated;
revoke all on sequence public.discord_trade_outbox_id_seq from anon, authenticated;
