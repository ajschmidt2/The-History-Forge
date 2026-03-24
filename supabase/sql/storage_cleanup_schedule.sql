-- Enable required extensions
create extension if not exists pg_cron;
create extension if not exists pg_net;

-- Optional: remove old job if it already exists
do $$
begin
  if exists (
    select 1
    from cron.job
    where jobname = 'daily-storage-cleanup'
  ) then
    perform cron.unschedule('daily-storage-cleanup');
  end if;
exception
  when others then
    null;
end $$;

-- Schedule the cleanup function daily at 03:00 UTC
select cron.schedule(
  'daily-storage-cleanup',
  '0 3 * * *',
  $$
  select
    net.http_post(
      url := 'https://YOUR_PROJECT_REF.supabase.co/functions/v1/storage-cleanup',
      headers := jsonb_build_object(
        'Content-Type', 'application/json',
        'Authorization', 'Bearer YOUR_STORAGE_CLEANUP_SECRET'
      ),
      body := '{}'::jsonb
    );
  $$
);
