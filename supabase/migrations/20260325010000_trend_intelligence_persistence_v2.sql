create table if not exists public.trend_scan_runs (
    id text primary key,
    user_id text not null,
    filters_json jsonb not null default '{}'::jsonb,
    started_at timestamptz not null default now(),
    completed_at timestamptz,
    status text not null default 'running',
    summary_json jsonb not null default '{}'::jsonb
);

create table if not exists public.trend_topic_results (
    id bigint generated always as identity primary key,
    scan_run_id text not null references public.trend_scan_runs(id) on delete cascade,
    topic_title text not null,
    score_total numeric(6,2) not null,
    score_breakdown_json jsonb not null default '{}'::jsonb,
    insight_json jsonb not null default '{}'::jsonb,
    source_json jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists public.saved_topic_candidates (
    id bigint generated always as identity primary key,
    user_id text not null,
    topic_title text not null,
    source_topic_result_id bigint references public.trend_topic_results(id) on delete set null,
    notes text not null default '',
    status text not null default 'saved',
    created_at timestamptz not null default now()
);

create index if not exists idx_trend_scan_runs_user_started
    on public.trend_scan_runs(user_id, started_at desc);

create index if not exists idx_trend_topic_results_scan_score
    on public.trend_topic_results(scan_run_id, score_total desc);

create index if not exists idx_saved_topic_candidates_user_created
    on public.saved_topic_candidates(user_id, created_at desc);
