create table if not exists public.referrals (
 id uuid primary key default gen_random_uuid(),
 referrer_id uuid not null references auth.users(id) on delete cascade,
 code text unique not null,
 reward_balance numeric default 0,
 successful_referrals integer default 0,
 created_at timestamptz default now()
);
alter table public.referrals enable row level security;
create unique index if not exists referrals_referrer_idx on public.referrals(referrer_id);
