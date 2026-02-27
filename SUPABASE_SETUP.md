# Supabase Setup Guide

This guide walks you through creating the Supabase project, database tables, and
storage buckets required by The History Forge.

---

## 1. Create a Supabase project

1. Go to [https://supabase.com](https://supabase.com) and sign in.
2. Click **New project** and fill in a name (e.g. `history-forge`).
3. Note your **Project URL** and **anon public key** from
   **Project Settings → API**.

---

## 2. Add credentials to Streamlit secrets

Edit `.streamlit/secrets.toml`:

```toml
SUPABASE_URL = "https://<your-ref>.supabase.co"
SUPABASE_KEY = "<your-anon-public-key>"
```

> **Never commit real keys.**  `.streamlit/secrets.toml` is already in `.gitignore`.

---

## 3. Run the SQL migrations

Open the **SQL Editor** in the Supabase dashboard and run the following:

```sql
-- ----------------------------------------------------------------
-- projects table
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT        PRIMARY KEY,
    title       TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Auto-update updated_at on every row change
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS projects_updated_at ON projects;
CREATE TRIGGER projects_updated_at
    BEFORE UPDATE ON projects
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ----------------------------------------------------------------
-- assets table
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS assets (
    id          BIGSERIAL   PRIMARY KEY,
    project_id  TEXT        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    asset_type  TEXT        NOT NULL,   -- 'image' | 'audio' | 'video'
    filename    TEXT        NOT NULL,
    url         TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, asset_type, filename)
);
```

---

## 4. Row Level Security (RLS)

If you want the anon key to be able to read and write, add permissive policies.
Run the following in the SQL Editor:

```sql
-- Enable RLS on both tables
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE assets   ENABLE ROW LEVEL SECURITY;

-- Allow all operations for the anon role (adjust as needed for production)
CREATE POLICY "anon full access" ON projects FOR ALL TO anon USING (true) WITH CHECK (true);
CREATE POLICY "anon full access" ON assets   FOR ALL TO anon USING (true) WITH CHECK (true);
```

---

## 5. Create Storage buckets

In the Supabase dashboard go to **Storage** and create these buckets:

| Bucket name              | Public? | Purpose                             |
|--------------------------|---------|-------------------------------------|
| `history-forge-images`   | Yes     | Generated scene images              |
| `history-forge-audio`    | Yes     | Voiceover & music files             |
| `history-forge-videos`   | Yes     | Rendered video exports              |
| `history-forge-scripts`  | Yes     | Generated script text files         |
| `generated-videos`       | Yes     | AI-generated videos (Veo / Sora)    |

For each bucket you also need a storage policy that allows the anon key to
upload.  In the **Policies** tab of each bucket add:

```sql
-- INSERT policy for anon uploads
CREATE POLICY "anon upload" ON storage.objects
    FOR INSERT TO anon
    WITH CHECK (bucket_id = '<bucket-name>');

-- SELECT policy for public reads
CREATE POLICY "public read" ON storage.objects
    FOR SELECT TO anon
    USING (bucket_id = '<bucket-name>');
```

Replace `<bucket-name>` with the actual bucket name for each policy.

> **Shortcut for `generated-videos`:** The file
> `migrations/001_generated_videos_bucket.sql` creates the bucket *and* its
> RLS policies in one step.  Paste it into the Supabase SQL Editor and run it.

---

## 6. AI Video Generation credentials

The **AI Video Generator** tab supports two providers.

### 6a) Google Veo via Supabase Edge Function (recommended)

Veo must run server-side so the frontend does **not** hold a short-lived Google
access token. This repository includes an Edge Function at
`supabase/functions/veo-generate/index.ts`.

Deploy it:

```bash
supabase functions deploy veo-generate
```

Set function secrets (never commit these to `.env`):

```bash
supabase secrets set GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
supabase secrets set GOOGLE_CLOUD_PROJECT_ID='my-project-123'
supabase secrets set GOOGLE_CLOUD_LOCATION='us-central1'
```

Then add only Supabase invocation secrets to `.streamlit/secrets.toml`:

```toml
SUPABASE_URL = "https://<your-ref>.supabase.co"
SUPABASE_KEY = "<your-anon-public-key>"
# Or use this key name if your existing setup already uses it:
# SUPABASE_ANON_KEY = "<your-anon-public-key>"
# Optional if you rename the function:
SUPABASE_VEO_FUNCTION_NAME = "veo-generate"

# --- OpenAI Sora ---
openai_api_key = "sk-..."
```

If a provider's credentials are missing the app shows a friendly warning and
disables that option in the provider selector.

---

## 7. Verify the setup

Start the app and navigate to the **Supabase Diagnostics** page in the
sidebar.  All five checks should pass.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|-------------|
| "Read failed" on the diagnostics page | Tables don't exist — re-run step 3 |
| "Write test failed" with 403 | RLS policy missing — run step 4 |
| Storage buckets not found | Buckets not created — complete step 5 |
| Client creation fails | Wrong URL or key — double-check step 2 |
