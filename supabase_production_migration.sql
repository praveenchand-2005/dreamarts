alter table public.orders add column if not exists courier text;
alter table public.orders add column if not exists tracking_number text;
create index if not exists orders_status_idx on public.orders(status);
