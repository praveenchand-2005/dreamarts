create table if not exists public.notifications (
 id uuid primary key default gen_random_uuid(),
 user_id uuid references auth.users(id) on delete cascade,
 order_number text,
 title text not null,
 message text not null,
 type text default 'order_update',
 read boolean default false,
 created_at timestamptz default now()
);
alter table public.notifications enable row level security;
drop policy if exists "users read own notifications" on public.notifications;
create policy "users read own notifications" on public.notifications for select to authenticated using (auth.uid() = user_id);
create index if not exists notifications_user_created_idx on public.notifications(user_id,created_at desc);
