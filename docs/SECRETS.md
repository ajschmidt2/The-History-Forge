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
