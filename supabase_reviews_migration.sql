create table if not exists public.order_reviews (
 id uuid primary key default gen_random_uuid(),
 order_number text unique not null references public.orders(order_number) on delete cascade,
 rating integer not null check (rating between 1 and 5),
 review text,
 gallery_permission boolean default false,
 created_at timestamptz default now(),
 updated_at timestamptz default now()
);
alter table public.order_reviews enable row level security;
create index if not exists order_reviews_rating_idx on public.order_reviews(rating);
