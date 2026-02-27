-- Migration 001: Add generated-videos storage bucket
--
-- Run this in the Supabase SQL Editor.
-- This creates the storage bucket for AI-generated videos (Veo / Sora)
-- and attaches the same RLS policies already used for the other media buckets.
--
-- Idempotent: safe to run more than once.

-- ----------------------------------------------------------------
-- 1. Create the bucket (skip if it already exists)
-- ----------------------------------------------------------------
INSERT INTO storage.buckets (id, name, public)
VALUES ('generated-videos', 'generated-videos', true)
ON CONFLICT (id) DO NOTHING;

-- ----------------------------------------------------------------
-- 2. RLS policies — anonymous read, authenticated write
--    (mirrors the pattern used for history-forge-images / audio / videos)
-- ----------------------------------------------------------------

-- Public (anon) read access
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'storage'
          AND tablename  = 'objects'
          AND policyname = 'generated-videos public read'
    ) THEN
        CREATE POLICY "generated-videos public read"
            ON storage.objects
            FOR SELECT
            TO anon
            USING (bucket_id = 'generated-videos');
    END IF;
END $$;

-- Anonymous upload (INSERT) — required because The History Forge uses the
-- anon key on the client.  Restrict further in production if needed.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'storage'
          AND tablename  = 'objects'
          AND policyname = 'generated-videos anon upload'
    ) THEN
        CREATE POLICY "generated-videos anon upload"
            ON storage.objects
            FOR INSERT
            TO anon
            WITH CHECK (bucket_id = 'generated-videos');
    END IF;
END $$;

-- Authenticated update / delete (future-proofing)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'storage'
          AND tablename  = 'objects'
          AND policyname = 'generated-videos authenticated write'
    ) THEN
        CREATE POLICY "generated-videos authenticated write"
            ON storage.objects
            FOR ALL
            TO authenticated
            USING (bucket_id = 'generated-videos')
            WITH CHECK (bucket_id = 'generated-videos');
    END IF;
END $$;
