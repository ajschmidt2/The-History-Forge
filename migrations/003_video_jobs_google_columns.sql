-- Migration 003: add Google Veo fields to public.video_jobs
-- Safe to run multiple times.

alter table public.video_jobs
add column if not exists provider text not null default 'openai'
check (provider in ('openai','google'));

alter table public.video_jobs
add column if not exists google_operation_name text null;

create unique index if not exists video_jobs_google_operation_name_unique
on public.video_jobs (google_operation_name)
where google_operation_name is not null;

alter table public.video_jobs
add column if not exists model text null;

alter table public.video_jobs
add column if not exists aspect_ratio text null;

alter table public.video_jobs
add column if not exists duration_seconds integer null;
