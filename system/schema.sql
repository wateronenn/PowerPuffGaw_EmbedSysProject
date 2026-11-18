-- Run once in Supabase SQL Editor. These new tables preserve old project tables.
create table if not exists public.solar_readings (
    id uuid primary key,
    device_id text not null,
    received_at timestamptz not null default now(),
    payload jsonb not null
);
create index if not exists solar_readings_device_time
    on public.solar_readings (device_id, received_at desc, id desc);

create table if not exists public.solar_reports (
    device_id text primary key,
    last_sent_at timestamptz not null,
    discord_message_id text not null
);

alter table public.solar_readings enable row level security;
alter table public.solar_reports enable row level security;
revoke all on public.solar_readings, public.solar_reports from anon, authenticated;
grant select, insert on public.solar_readings to service_role;
grant select, insert, update on public.solar_reports to service_role;
