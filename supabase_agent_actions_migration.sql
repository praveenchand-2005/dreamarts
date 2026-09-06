create table if not exists public.agent_action_log (
 id uuid primary key default gen_random_uuid(),
 order_number text references public.orders(order_number) on delete cascade,
 action_type text not null,
 status text not null default 'EXECUTED',
 created_at timestamptz default now()
);
alter table public.agent_action_log enable row level security;
create index if not exists agent_action_log_order_idx on public.agent_action_log(order_number);
