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

    optional_veo = bool(get_secret("GOOGLE_VEO_API_KEY") or get_secret("VEO_API_KEY"))
    optional_sora = bool(openai["api_key"])

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
            "- Optional Veo keys: GOOGLE_VEO_API_KEY / VEO_API_KEY\n"
            "- Optional Sora: requires OPENAI_API_KEY."
        )

    return {
        "required": required_checks,
        "features": {
            "veo_enabled": optional_veo,
            "sora_enabled": optional_sora,
        },
        "buckets": {
            "images_bucket": str(supabase["images_bucket"] or ""),
            "audio_bucket": str(supabase["audio_bucket"] or ""),
            "videos_bucket": str(supabase["videos_bucket"] or ""),
        },
    }
