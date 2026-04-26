# Gemini Developer API Migration

## Summary

History Forge now routes Gemini text, image, and video model access through `src/providers/gemini_provider.py` using the Google GenAI SDK and `GEMINI_API_KEY`.

Removed generative paths that required Google Cloud project/location/service-account setup:

- Supabase `veo-generate` Edge Function
- Supabase `veo-image-to-video` Edge Function
- Local REST calls to the former Google Cloud model endpoint

## Required Environment

```toml
GEMINI_API_KEY = "AIza..."
GEMINI_MODEL_TEXT = "gemini-2.5-flash"
GEMINI_MODEL_FAST = "gemini-2.5-flash"
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"
GEMINI_VIDEO_MODEL = "veo-3.1-lite-generate-preview"
```

OpenAI remains responsible for scripts and voiceover. Supabase remains responsible for app storage.

## Manual Steps

1. Create a Gemini API key in Google AI Studio.
2. Add `GEMINI_API_KEY` to local `.streamlit/secrets.toml`, Streamlit Cloud secrets, or Vercel environment variables.
3. Remove old generative Google Cloud secrets if present: service-account JSON, project, location, and application credentials.
4. Redeploy/restart the app.

## Verification

- Provider SDK smoke test: `generate_fast_text(...)` returned `Gemini provider OK`.
- Gemini Veo Lite smoke test generated `data/google_veo_video_tests/genai_sdk_veo_lite_smoke.mp4`.
- Existing shorts workflow render completed at `data/projects/auto-2026-03-14/renders/final.mp4`.

## Rollback

Restore the previous `src/services/google_veo_video.py` implementation and the deleted Supabase Edge Functions from source control or backup, then restore the old Google Cloud service-account/project/location secrets.
