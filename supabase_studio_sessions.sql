-- Dreamarts persistent Studio sessions
create table if not exists public.studio_sessions (
  id uuid primary key,
  user_id uuid null,
  config jsonb not null default '{}'::jsonb,
  variants jsonb not null default '[]'::jsonb,
  selected_variant integer not null default 0,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);
alter table public.studio_sessions enable row level security;
drop policy if exists "studio sessions anonymous insert" on public.studio_sessions;
drop policy if exists "studio sessions anonymous read" on public.studio_sessions;
drop policy if exists "studio sessions anonymous update" on public.studio_sessions;
create policy "studio sessions anonymous insert" on public.studio_sessions for insert to anon, authenticated with check (true);
create policy "studio sessions anonymous read" on public.studio_sessions for select to anon, authenticated using (true);
create policy "studio sessions anonymous update" on public.studio_sessions for update to anon, authenticated using (true) with check (true);
