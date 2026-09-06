create table if not exists public.agent_tasks (
 id uuid primary key default gen_random_uuid(),
 agent text not null,
 title text not null,
 priority text not null,
 order_number text references public.orders(order_number) on delete cascade,
 status text not null default 'PENDING',
 requires_approval boolean default true,
 proposed_action text,
 created_at timestamptz default now(),
 approved_at timestamptz
);
alter table public.agent_tasks enable row level security;
create index if not exists agent_tasks_status_idx on public.agent_tasks(status);
create index if not exists agent_tasks_agent_idx on public.agent_tasks(agent);
