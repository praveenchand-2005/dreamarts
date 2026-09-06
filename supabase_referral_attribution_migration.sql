create table if not exists public.referral_attributions (
 id uuid primary key default gen_random_uuid(),
 referral_code text not null,
 referrer_id uuid not null references auth.users(id) on delete cascade,
 referred_customer_id uuid unique not null references auth.users(id) on delete cascade,
 status text not null default 'ATTRIBUTED',
 qualified_order_number text references public.orders(order_number),
 reward_amount numeric default 0,
 created_at timestamptz default now(),
 qualified_at timestamptz
);
alter table public.referral_attributions enable row level security;
create index if not exists referral_attributions_referrer_idx on public.referral_attributions(referrer_id);
