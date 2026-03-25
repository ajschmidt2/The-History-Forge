create table if not exists public.trend_intelligence_scans (
    id text primary key,
    project_id text not null,
    source_names text[] not null default '{}',
    status text not null default 'running',
    error_message text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.trend_intelligence_topics (
    id bigint generated always as identity primary key,
    scan_id text not null references public.trend_intelligence_scans(id) on delete cascade,
    topic_title text not null,
    total_score numeric(5,2) not null,
    momentum_score numeric(4,2) not null,
    watch_time_score numeric(4,2) not null,
    clickability_score numeric(4,2) not null,
    competition_gap_score numeric(4,2) not null,
    brand_alignment_score numeric(4,2) not null,
    why_trending text not null,
    content_angles jsonb not null default '[]'::jsonb,
    suggested_hooks jsonb not null default '[]'::jsonb,
    thumbnail_ideas jsonb not null default '[]'::jsonb,
    trend_source text not null,
    youtube_video_count int not null default 0,
    created_at timestamptz not null default now()
);

create index if not exists idx_trend_scans_project_created
    on public.trend_intelligence_scans(project_id, created_at desc);

create index if not exists idx_trend_topics_scan_score
    on public.trend_intelligence_topics(scan_id, total_score desc);
