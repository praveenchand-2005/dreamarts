create table if not exists public.order_qc (
 id uuid primary key default gen_random_uuid(),
 order_number text unique not null references public.orders(order_number) on delete cascade,
 nail_alignment boolean default false,
 string_tension boolean default false,
 design_match boolean default false,
 frame_condition boolean default false,
 final_photo_url text,
 notes text,
 qc_status text default 'PENDING',
 created_at timestamptz default now(),
 updated_at timestamptz default now()
);
alter table public.order_qc enable row level security;
create index if not exists order_qc_order_idx on public.order_qc(order_number);
