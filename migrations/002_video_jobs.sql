-- Migration 002: track OpenAI Sora video jobs
-- Run this in Supabase SQL editor.

CREATE TABLE IF NOT EXISTS public.video_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NULL,
    openai_video_id text NOT NULL UNIQUE,
    prompt text,
    status text NOT NULL CHECK (status IN ('queued', 'in_progress', 'completed', 'failed')),
    progress numeric NULL,
    error text NULL,
    bucket text NOT NULL DEFAULT 'videos',
    storage_path text NULL,
    public_url text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_video_jobs_user_id ON public.video_jobs (user_id);
CREATE INDEX IF NOT EXISTS idx_video_jobs_status ON public.video_jobs (status);

CREATE OR REPLACE FUNCTION public.set_video_jobs_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_video_jobs_updated_at ON public.video_jobs;
CREATE TRIGGER trg_video_jobs_updated_at
BEFORE UPDATE ON public.video_jobs
FOR EACH ROW
EXECUTE FUNCTION public.set_video_jobs_updated_at();
