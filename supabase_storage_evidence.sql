insert into storage.buckets (id,name,public) values ('production-evidence','production-evidence',true) on conflict (id) do nothing;
drop policy if exists "authenticated upload production evidence" on storage.objects;
create policy "authenticated upload production evidence" on storage.objects for insert to authenticated with check (bucket_id='production-evidence');
drop policy if exists "public read production evidence" on storage.objects;
create policy "public read production evidence" on storage.objects for select using (bucket_id='production-evidence');
