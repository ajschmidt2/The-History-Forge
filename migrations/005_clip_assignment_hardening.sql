-- Migration 005: Harden clip-assignment persistence
--
-- Addresses two potential causes of "Supabase save failed" on clip assignments:
--
--   1. Missing unique constraint on assets(project_id, asset_type, filename).
--      The initial SUPABASE_SETUP.md SQL includes this UNIQUE clause, but some
--      projects may have been created from an older schema.  Adding it
--      idempotently here ensures the ON CONFLICT upsert in save_clip_assignment
--      works correctly.
--
--   2. Missing UPDATE / DELETE RLS policies on the assets table.
--      When RLS is enabled, anon users need UPDATE permission for upserts that
--      match an existing row (ON CONFLICT DO UPDATE requires UPDATE privilege).
--
-- Safe to run more than once.

-- ----------------------------------------------------------------
-- 1. Ensure the unique constraint exists
-- ----------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.assets'::regclass
          AND contype  = 'u'
          AND conname  = 'assets_project_asset_filename_unique'
    ) THEN
        -- Check if there is already an unnamed unique constraint on these columns
        -- by looking for a matching unique index.
        IF NOT EXISTS (
            SELECT 1
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_attribute a ON a.attrelid = c.oid
            WHERE c.relname = 'assets'
              AND i.indisunique
              AND array_to_string(
                      ARRAY(
                          SELECT pg_attribute.attname
                          FROM pg_attribute
                          WHERE pg_attribute.attrelid = c.oid
                            AND pg_attribute.attnum = ANY(i.indkey)
                          ORDER BY pg_attribute.attnum
                      ), ','
                  ) = 'project_id,asset_type,filename'
        ) THEN
            ALTER TABLE public.assets
                ADD CONSTRAINT assets_project_asset_filename_unique
                UNIQUE (project_id, asset_type, filename);
        END IF;
    END IF;
END $$;

-- ----------------------------------------------------------------
-- 2. Add UPDATE RLS policy for anon (required for upsert / ON CONFLICT DO UPDATE)
-- ----------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'assets'
          AND policyname = 'anon update assets'
    ) THEN
        CREATE POLICY "anon update assets"
            ON public.assets
            FOR UPDATE
            TO anon
            USING (true)
            WITH CHECK (true);
    END IF;
END $$;

-- ----------------------------------------------------------------
-- 3. Add UPDATE RLS policy on projects for anon (needed for project upsert)
-- ----------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'projects'
          AND policyname = 'anon update projects'
    ) THEN
        CREATE POLICY "anon update projects"
            ON public.projects
            FOR UPDATE
            TO anon
            USING (true)
            WITH CHECK (true);
    END IF;
END $$;

-- ----------------------------------------------------------------
-- 4. Ensure history-forge-videos bucket allows UPDATE (for upsert/overwrite)
-- ----------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'storage'
          AND tablename  = 'objects'
          AND policyname = 'history-forge-videos anon update'
    ) THEN
        CREATE POLICY "history-forge-videos anon update"
            ON storage.objects
            FOR UPDATE
            TO anon
            USING (bucket_id = 'history-forge-videos')
            WITH CHECK (bucket_id = 'history-forge-videos');
    END IF;
END $$;
