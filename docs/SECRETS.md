# Secrets configuration

Use the centralized loader in `src/config/secrets.py`. Do not read `st.secrets` or environment variables directly in app code.

## Streamlit secrets TOML (supported formats)

### Pattern A (flat)
```toml
SUPABASE_URL="https://xxxx.supabase.co"
SUPABASE_ANON_KEY="..."
OPENAI_API_KEY="..."
```

### Pattern B (nested)
```toml
[supabase]
url="https://xxxx.supabase.co"
anon_key="..."

[openai]
api_key="..."
```

## Environment variable fallback

For local development, you can export the same keys as env vars (aliases are supported by the loader).

## Video provider keys

- `FAL_API_KEY` (or `FAL_KEY`) for fal.ai Wan image-to-video.
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) for Google Gemini Veo 3.1 Lite preview.
- Optional `HF_VIDEO_PROVIDER` default provider override (e.g. `falai`, `google_veo_lite`, `auto`).
- Optional `HF_GOOGLE_VIDEO_MODEL` default model override (defaults to `veo-3.1-lite-generate-preview`).
