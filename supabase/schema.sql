-- DREAMARTS PRODUCTION DATABASE
create extension if not exists pgcrypto;

create table if not exists public.profiles (
 id uuid primary key references auth.users(id) on delete cascade,
 full_name text,
 phone text,
 role text not null default 'customer' check (role in ('customer','admin')),
 created_at timestamptz not null default now(),
 updated_at timestamptz not null default now()
);

create table if not exists public.orders (
 id uuid primary key default gen_random_uuid(),
 order_number text not null unique,
 user_id uuid not null references auth.users(id) on delete cascade,
 status text not null default 'NEW_REQUEST',
 artwork_shape text,
 artwork_width_mm integer,
 artwork_height_mm integer,
 anchor_nails integer,
 string_lines integer,
 notes text,
 created_at timestamptz not null default now(),
 updated_at timestamptz not null default now()
);

create table if not exists public.order_history (
 id uuid primary key default gen_random_uuid(),
 order_id uuid not null references public.orders(id) on delete cascade,
 status text not null,
 note text,
 created_at timestamptz not null default now()
);

create table if not exists public.artwork_files (
 id uuid primary key default gen_random_uuid(),
 order_id uuid not null references public.orders(id) on delete cascade,
 file_type text not null check (file_type in ('original','proof','final')),
 storage_path text not null,
 created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;
alter table public.orders enable row level security;
alter table public.order_history enable row level security;
alter table public.artwork_files enable row level security;

drop policy if exists "profile select own" on public.profiles;
create policy "profile select own" on public.profiles for select using (auth.uid() = id);
drop policy if exists "profile update own" on public.profiles;
create policy "profile update own" on public.profiles for update using (auth.uid() = id);
drop policy if exists "profile insert own" on public.profiles;
create policy "profile insert own" on public.profiles for insert with check (auth.uid() = id);

drop policy if exists "orders select own" on public.orders;
create policy "orders select own" on public.orders for select using (auth.uid() = user_id);
drop policy if exists "orders insert own" on public.orders;
create policy "orders insert own" on public.orders for insert with check (auth.uid() = user_id);

drop policy if exists "history select own order" on public.order_history;
create policy "history select own order" on public.order_history for select using (
 exists(select 1 from public.orders o where o.id=order_id and o.user_id=auth.uid())
);

drop policy if exists "files select own order" on public.artwork_files;
create policy "files select own order" on public.artwork_files for select using (
 exists(select 1 from public.orders o where o.id=order_id and o.user_id=auth.uid())
);

-- Automatically create customer profile after signup
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path=public as $$
begin
 insert into public.profiles(id,full_name)
 values(new.id,coalesce(new.raw_user_meta_data->>'full_name',''))
 on conflict(id) do nothing;
 return new;
end;$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
for each row execute procedure public.handle_new_user();


-- PRIVATE CUSTOMER ARTWORK STORAGE
insert into storage.buckets (id,name,public) values ('artwork-uploads','artwork-uploads',false) on conflict (id) do nothing;

drop policy if exists "customers upload own artwork" on storage.objects;
create policy "customers upload own artwork" on storage.objects for insert to authenticated with check (
 bucket_id='artwork-uploads' and (storage.foldername(name))[1]=auth.uid()::text
);
drop policy if exists "customers view own artwork" on storage.objects;
create policy "customers view own artwork" on storage.objects for select to authenticated using (
 bucket_id='artwork-uploads' and (storage.foldername(name))[1]=auth.uid()::text
);
drop policy if exists "files insert own order" on public.artwork_files;
create policy "files insert own order" on public.artwork_files for insert with check (
 exists(select 1 from public.orders o where o.id=order_id and o.user_id=auth.uid())
);
