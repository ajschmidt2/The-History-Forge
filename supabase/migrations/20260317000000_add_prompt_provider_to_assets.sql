-- Add prompt and provider columns to assets table if they don't exist
ALTER TABLE assets
  ADD COLUMN IF NOT EXISTS prompt   TEXT,
  ADD COLUMN IF NOT EXISTS provider TEXT;

-- Optional: index provider for filtering
CREATE INDEX IF NOT EXISTS assets_provider_idx ON assets (provider);
