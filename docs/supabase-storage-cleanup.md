# Supabase Storage Cleanup (Edge Function)

This repository includes a Supabase Edge Function at:

- `supabase/functions/storage-cleanup/index.ts`

It scans configured Storage buckets, finds files older than each bucket's retention window, and deletes those files using the Supabase Storage API (`storage.from(bucket).remove(paths)`).

## Retention policy

- `uploads`: 7 days
- `videos`: 7 days
- `generated-videos`: 3 days
- `history-forge-scripts`: 30 days
- `history-forge-videos`: 7 days
- `history-forge-audio`: 7 days
- `history-forge-images`: 7 days

## What the function does

- Requires a custom bearer token (`STORAGE_CLEANUP_SECRET`) on every request.
- Uses `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` from environment variables.
- Recursively scans bucket contents (including nested folders).
- Uses `created_at` to determine file age, with fallback to `updated_at`.
- Deletes old files in batches of 100.
- Returns per-bucket JSON results, for example:

```json
{
  "ok": true,
  "results": {
    "uploads": {
      "retention_days": 7,
      "files_found_for_deletion": 5,
      "deleted": 5
    }
  }
}
```

## Required function secrets

In the Supabase Dashboard, add these Edge Function secrets:

- `STORAGE_CLEANUP_SECRET` (custom secret used in `Authorization: Bearer ...`)
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

> Ensure `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are available to the function environment.

## Deploy after merge (GitHub-only workflow)

After this change is merged, deploy from your Supabase project (Dashboard or CLI).

Example CLI flow:

```bash
supabase functions deploy storage-cleanup
```

If you deploy from the Dashboard UI, select the `storage-cleanup` function source and publish it.

## Manually schedule the cleanup job

Schedule the function to run daily (for example, at **3:00 AM UTC**) using either:

- Supabase Dashboard scheduler UI, or
- SQL with `pg_cron` + `pg_net`

### Scheduler target

Call:

```text
https://<project-ref>.supabase.co/functions/v1/storage-cleanup
```

with header:

```text
Authorization: Bearer <STORAGE_CLEANUP_SECRET>
```

### SQL example (`pg_cron` + `pg_net`)

```sql
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
```


### Manual SQL Editor steps

1. Open Supabase Dashboard -> SQL Editor.
2. Paste the contents of `supabase/sql/storage_cleanup_schedule.sql`.
3. Replace:
   - `YOUR_PROJECT_REF`
   - `YOUR_STORAGE_CLEANUP_SECRET`
4. Run the SQL.
5. Confirm the job exists by running:

```sql
select jobid, jobname, schedule, command
from cron.job
order by jobid desc;
```

6. To remove the schedule later, run:

```sql
select cron.unschedule('daily-storage-cleanup');
```

### Important safety note

- The cleanup function itself must delete files through the Storage API.
- The SQL job should only invoke the Edge Function endpoint.
- Do not write SQL that deletes rows from `storage.objects`.


## Manual test request

After deploying and setting secrets, you can manually trigger it:

```bash
curl -X POST \
  'https://<project-ref>.supabase.co/functions/v1/storage-cleanup' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <STORAGE_CLEANUP_SECRET>' \
  -d '{}'
```

Expected behavior:

- HTTP `401` if authorization header is missing/invalid.
- HTTP `200` with per-bucket cleanup stats if authorized.
