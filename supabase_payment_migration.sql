-- Optional payment audit fields for Dreamarts
alter table public.orders add column if not exists razorpay_payment_id text;
alter table public.orders add column if not exists payment_verified_at timestamptz;
