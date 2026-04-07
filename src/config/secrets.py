from __future__ import annotations

"""Do not read secrets via st.secrets or os.getenv directly anywhere else. Always use get_secret()."""

import os
import tomllib
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

_ALIAS_MAP: dict[str, list[str]] = {
    "SUPABASE_URL": ["SUPABASE_URL", "supabase_url", "SUPABASE__URL"],
    "SUPABASE_ANON_KEY": ["SUPABASE_ANON_KEY", "SUPABASE_KEY", "supabase_anon_key", "supabase_key"],
    "SUPABASE_SERVICE_ROLE_KEY": ["SUPABASE_SERVICE_ROLE_KEY", "supabase_service_role_key"],
    "OPENAI_API_KEY": ["OPENAI_API_KEY", "openai_api_key"],
    "IMAGES_BUCKET": ["IMAGES_BUCKET", "images_bucket"],
    "AUDIO_BUCKET": ["AUDIO_BUCKET", "audio_bucket"],
    "VIDEOS_BUCKET": ["VIDEOS_BUCKET", "videos_bucket"],
    "PEXELS_API_KEY": ["PEXELS_API_KEY", "pexels_api_key", "PEXELS_KEY", "pexels_key"],
    "PIXABAY_API_KEY": ["PIXABAY_API_KEY", "pixabay_api_key", "PIXABAY_KEY", "pixabay_key"],
    "YOUTUBE_CLIENT_SECRETS_FILE": ["YOUTUBE_CLIENT_SECRETS_FILE", "youtube_client_secrets_file"],
    "YOUTUBE_TOKEN_FILE": ["YOUTUBE_TOKEN_FILE", "youtube_token_file"],
    "YOUTUBE_CLIENT_SECRETS_FILE_CONSPIRACY": ["YOUTUBE_CLIENT_SECRETS_FILE_CONSPIRACY", "youtube_client_secrets_file_conspiracy"],
    "YOUTUBE_TOKEN_FILE_CONSPIRACY": ["YOUTUBE_TOKEN_FILE_CONSPIRACY", "youtube_token_file_conspiracy"],
    "META_APP_ID": ["META_APP_ID", "meta_app_id"],
    "META_APP_SECRET": ["META_APP_SECRET", "meta_app_secret"],
    "INSTAGRAM_USER_ID": ["INSTAGRAM_USER_ID", "instagram_user_id"],
    "INSTAGRAM_ACCESS_TOKEN": ["INSTAGRAM_ACCESS_TOKEN", "instagram_access_token"],
    "TIKTOK_ACCESS_TOKEN": ["TIKTOK_ACCESS_TOKEN", "tiktok_access_token"],
    "TIKTOK_OPEN_ID": ["TIKTOK_OPEN_ID", "tiktok_open_id"],
    "FAL_API_KEY": ["FAL_API_KEY", "fal_api_key", "FAL_KEY", "fal_key"],
}

_NESTED_STREAMLIT_PATHS: dict[str, tuple[tuple[str, ...], ...]] = {
    "SUPABASE_URL": (("supabase", "url"),),
    "SUPABASE_ANON_KEY": (("supabase", "anon_key"), ("supabase", "key")),
    "SUPABASE_SERVICE_ROLE_KEY": (("supabase", "service_role_key"),),
    "OPENAI_API_KEY": (("openai", "api_key"),),
    "IMAGES_BUCKET": (("supabase", "images_bucket"), ("buckets", "images")),
    "AUDIO_BUCKET": (("supabase", "audio_bucket"), ("buckets", "audio")),
    "VIDEOS_BUCKET": (("supabase", "videos_bucket"), ("buckets", "videos")),
    "PEXELS_API_KEY": (
        ("pexels", "api_key"),
        ("broll", "pexels_api_key"),
        ("api_keys", "pexels"),
        ("api_keys", "pexels_api_key"),
    ),
    "PIXABAY_API_KEY": (
        ("pixabay", "api_key"),
        ("broll", "pixabay_api_key"),
        ("api_keys", "pixabay"),
        ("api_keys", "pixabay_api_key"),
    ),
    "YOUTUBE_CLIENT_SECRETS_FILE": (("youtube", "client_secrets_file"),),
    "YOUTUBE_TOKEN_FILE": (("youtube", "token_file"),),
    "FAL_API_KEY": (
        ("fal", "api_key"),
        ("fal", "key"),
        ("api_keys", "fal"),
        ("api_keys", "fal_api_key"),
    ),
}

_PLACEHOLDER_VALUES = {"", "none", "null", "paste_key_here", "your_api_key_here", "replace_me"}


@lru_cache(maxsize=None)
def _load_toml_secrets() -> dict:
    """Load .streamlit/secrets.toml using stdlib tomllib (no Streamlit runtime required)."""
    toml_path = Path(".streamlit/secrets.toml")
    if toml_path.exists():
        try:
            with open(toml_path, "rb") as f:
                return tomllib.load(f)
        except Exception:
            return {}
    return {}


def _normalize(value: Any) -> str:
    cleaned = str(value or "").strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1].strip()
    if cleaned.lower() in _PLACEHOLDER_VALUES:
        return ""
    return cleaned


def _safe_streamlit_secrets() -> Any | None:
    try:
        import streamlit as st  # type: ignore

        secrets = getattr(st, "secrets", None)
        if secrets is None:
            return None
        return secrets
    except Exception:
        return None


def streamlit_secrets_detected() -> bool:
    return _safe_streamlit_secrets() is not None


def _read_key(container: Any, key: str) -> tuple[bool, Any]:
    """Return (found, value) for dict-like and attrdict-like containers."""
    if isinstance(container, Mapping):
        if key in container:
            return True, container[key]
        return False, None

    # Streamlit's secrets object supports key access but may not implement Mapping.
    try:
        value = container[key]
    except Exception:
        return False, None
    return True, value


def _mapping_path_get(mapping: Any, path: tuple[str, ...]) -> str:
    current = mapping
    for key in path:
        found, value = _read_key(current, key)
        if not found:
            return ""
        current = value
    return _normalize(current)


def _read_streamlit_secret(key: str) -> str | None:
    secrets = _safe_streamlit_secrets()
    if secrets is None:
        return None

    try:
        found, raw_value = _read_key(secrets, key)
        value = _normalize(raw_value) if found else ""
        if value:
            return value

        if key in _NESTED_STREAMLIT_PATHS:
            for path in _NESTED_STREAMLIT_PATHS[key]:
                nested = _mapping_path_get(secrets, path)
                if nested:
                    return nested
    except Exception:
        return None

    return None


def _read_env(key: str) -> str | None:
    value = _normalize(os.environ.get(key, ""))
    return value or None


def _aliases(name: str) -> list[str]:
    canonical = name.upper()
    aliases = _ALIAS_MAP.get(canonical, [name, name.lower(), canonical])
    seen: set[str] = set()
    ordered: list[str] = []
    for alias in aliases:
        if alias not in seen:
            ordered.append(alias)
            seen.add(alias)
    return ordered




def resolve_openai_key() -> str:
    """
    Resolve OpenAI API key.
    Order: env vars → .streamlit/secrets.toml → st.secrets (Streamlit runtime only)
    This order ensures headless/MCP/CI execution works without a Streamlit context.
    """
    # 1. Environment variables first (headless / MCP / CI)
    import os
    for k in ("OPENAI_API_KEY", "openai_api_key"):
        v = os.getenv(k, "").strip()
        if v:
            return v
    # 2. .streamlit/secrets.toml parsed directly (headless fallback)
    toml_secrets = _load_toml_secrets()
    for k in ("OPENAI_API_KEY", "openai_api_key"):
        v = str(toml_secrets.get(k, "")).strip()
        if v:
            return v
    # 3. st.secrets — only when running inside a live Streamlit runtime
    try:
        import streamlit as st
        for k in ("OPENAI_API_KEY", "openai_api_key"):
            v = st.secrets.get(k, None)
            if v is not None and str(v).strip():
                return str(v).strip()
    except Exception:
        pass
    return ""

def get_secret(key: str, default=None):
    # 1. Streamlit secrets (Streamlit Cloud and local with secrets.toml)
    try:
        import streamlit as st
        val = st.secrets.get(key)
        if val:
            return val
    except Exception:
        pass

    # 2. Environment variable (local dev, cron, Claude Code)
    val = os.environ.get(key)
    if val:
        return val

    # 3. Direct toml read (fallback for headless/cron mode)
    try:
        import tomllib
        toml_path = Path(__file__).parent.parent.parent / ".streamlit" / "secrets.toml"
        with open(toml_path, "rb") as f:
            toml_data = tomllib.load(f)
        val = toml_data.get(key)
        if val:
            return val
    except Exception:
        pass

    # 4. Return default if nothing found
    return default


def require_secrets(names: list[str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        value = get_secret(name)
        if value:
            resolved[name] = value
        else:
            missing.append(name)

    if missing:
        details = [f"- {name}: {', '.join(_aliases(name))}" for name in missing]
        raise RuntimeError(
            "Missing required secrets:\n"
            + "\n".join(details)
            + "\nSet these in Streamlit secrets or environment variables."
        )
    return resolved


def get_supabase_config() -> dict[str, str | None]:
    anon_key = get_secret("SUPABASE_ANON_KEY")
    service_role_key = get_secret("SUPABASE_SERVICE_ROLE_KEY")
    return {
        "url": get_secret("SUPABASE_URL"),
        "anon_key": anon_key,
        "service_role_key": service_role_key,
        "key": anon_key or service_role_key,
        "images_bucket": get_secret("IMAGES_BUCKET", "history-forge-images"),
        "audio_bucket": get_secret("AUDIO_BUCKET", "history-forge-audio"),
        "videos_bucket": get_secret("VIDEOS_BUCKET", "generated-videos"),
    }


def fal_configured() -> bool:
    """Return True if a fal.ai API key is present."""
    return bool(get_secret("fal_api_key"))


def get_openai_config() -> dict[str, str | None]:
    return {
        "api_key": get_secret("OPENAI_API_KEY"),
        "org": get_secret("OPENAI_ORG"),
        "project": get_secret("OPENAI_PROJECT"),
    }
