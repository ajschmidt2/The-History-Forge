-- Migration 004: Clip assignments
--
-- Clip assignments are stored as rows in the existing ``assets`` table using
-- asset_type = 'clip_assignment'.  No schema change is required — this
-- migration just documents the convention and ensures the necessary RLS
-- policy allows anonymous reads so the Streamlit app (which uses the anon key)
-- can query assignments.
--
-- The existing upsert conflict target ``(project_id, asset_type, filename)``
-- guarantees one assignment per scene per project.
--
-- Column usage for clip_assignment rows
-- -------------------------------------
--   project_id   TEXT   — the project UUID / slug
--   asset_type   TEXT   — always 'clip_assignment'
--   filename     TEXT   — scene key: 's01', 's02', … 's99'
--   url          TEXT   — public URL of the assigned effects clip
--
-- If the ``assets`` table does not yet have a unique constraint on the
-- three-column combination, add it below:
--
-- ALTER TABLE assets
--   ADD CONSTRAINT assets_project_asset_filename_unique
--   UNIQUE (project_id, asset_type, filename);
--
-- (Skip this block if the constraint already exists — the upsert in
-- supabase_storage.py will handle idempotency via ON CONFLICT.)

-- Ensure the history-forge-videos bucket exists (idempotent)
INSERT INTO storage.buckets (id, name, public)
VALUES ('history-forge-videos', 'history-forge-videos', true)
ON CONFLICT (id) DO NOTHING;

-- Allow anon to read effects-clip objects (thumbnails + clip URLs)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'storage'
          AND tablename  = 'objects'
          AND policyname = 'history-forge-videos public read'
    ) THEN
        CREATE POLICY "history-forge-videos public read"
            ON storage.objects
            FOR SELECT
            TO anon
            USING (bucket_id = 'history-forge-videos');
    END IF;
END $$;

-- Allow anon to upload effects clips and thumbnails
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'storage'
          AND tablename  = 'objects'
          AND policyname = 'history-forge-videos anon upload'
    ) THEN
        CREATE POLICY "history-forge-videos anon upload"
            ON storage.objects
            FOR INSERT
            TO anon
            WITH CHECK (bucket_id = 'history-forge-videos');
    END IF;
END $$;
