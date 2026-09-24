-- Layer 4 audit table. Production runtime connects as final14_bot.
create table if not exists public.system_events (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  event_key text not null,
  layer text not null check (layer in ('L1','L2','L3','L4')),
  severity text not null check (severity in ('INFO','WARNING','ERROR','CRITICAL')),
  event_id text,
  symbol text,
  combo integer,
  order_id text,
  message text not null default '',
  details jsonb not null default '{}'::jsonb
);

create index if not exists system_events_created_at_idx
  on public.system_events (created_at desc);
create index if not exists system_events_event_id_idx
  on public.system_events (event_id) where event_id is not null;
create index if not exists system_events_event_key_idx
  on public.system_events (event_key, created_at desc);

alter table public.system_events enable row level security;
revoke all on table public.system_events from anon, authenticated;
grant select, insert on table public.system_events to final14_bot;

drop policy if exists final14_bot_read_events on public.system_events;
create policy final14_bot_read_events
  on public.system_events for select to final14_bot using (true);

drop policy if exists final14_bot_insert_events on public.system_events;
create policy final14_bot_insert_events
  on public.system_events for insert to final14_bot with check (true);
