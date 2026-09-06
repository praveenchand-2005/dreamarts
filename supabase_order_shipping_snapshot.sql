alter table public.orders add column if not exists shipping_address jsonb;
create index if not exists orders_shipping_address_idx on public.orders using gin (shipping_address);
