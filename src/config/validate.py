from __future__ import annotations

from src.config.secrets import get_openai_config, get_secret, get_supabase_config


def validate_runtime_config() -> dict[str, dict[str, bool]]:
    supabase = get_supabase_config()
    openai = get_openai_config()

    required_checks = {
        "SUPABASE_URL": bool(supabase["url"]),
        "SUPABASE_ANON_KEY_OR_SERVICE_ROLE": bool(supabase["anon_key"] or supabase["service_role_key"]),
        "OPENAI_API_KEY": bool(openai["api_key"]),
    }

    optional_gemini_video = bool(get_secret("GEMINI_API_KEY") or get_secret("GOOGLE_API_KEY"))
    optional_fal = bool(get_secret("FAL_KEY") or get_secret("FAL_API_KEY") or get_secret("fal_api_key"))

    missing = [name for name, ok in required_checks.items() if not ok]
    if missing:
        raise RuntimeError(
            "Runtime configuration is incomplete. Missing required keys: "
            + ", ".join(missing)
            + "\n\nSet them in Streamlit secrets or environment variables.\n"
            "Expected names:\n"
            "- SUPABASE_URL: SUPABASE_URL / supabase_url / SUPABASE__URL\n"
            "- Supabase key: SUPABASE_ANON_KEY / SUPABASE_KEY / supabase_anon_key / supabase_key\n"
            "- Optional Supabase service key: SUPABASE_SERVICE_ROLE_KEY / supabase_service_role_key\n"
            "- OpenAI key: OPENAI_API_KEY / openai_api_key\n"
            "- Optional Gemini media key: GEMINI_API_KEY / GOOGLE_API_KEY\n"
            "- Optional fal.ai fallback key: FAL_KEY / FAL_API_KEY / fal_api_key."
        )

    return {
        "required": required_checks,
        "features": {
            "gemini_video_enabled": optional_gemini_video,
            "fal_fallback_enabled": optional_fal,
        },
        "buckets": {
            "images_bucket": str(supabase["images_bucket"] or ""),
            "audio_bucket": str(supabase["audio_bucket"] or ""),
            "videos_bucket": str(supabase["videos_bucket"] or ""),
        },
    }
