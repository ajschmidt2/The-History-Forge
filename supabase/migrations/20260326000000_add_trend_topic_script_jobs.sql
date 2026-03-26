create table if not exists public.trend_topic_script_jobs (
    id bigint generated always as identity primary key,
    user_id text not null,
    project_id text not null,
    topic_title text not null,
    why_may_be_trending text not null default '',
    preferred_content_angle text not null default '',
    selected_hook text not null default '',
    thumbnail_direction text not null default '',
    score_breakdown_json jsonb not null default '{}'::jsonb,
    source_topic_result_id bigint references public.trend_topic_results(id) on delete set null,
    source_scan_run_id text references public.trend_scan_runs(id) on delete set null,
    saved_topic_candidate_id bigint references public.saved_topic_candidates(id) on delete set null,
    status text not null default 'queued',
    created_at timestamptz not null default now()
);

create index if not exists idx_trend_topic_script_jobs_project_created
    on public.trend_topic_script_jobs(project_id, created_at desc);

create index if not exists idx_trend_topic_script_jobs_source_topic
    on public.trend_topic_script_jobs(source_topic_result_id);
