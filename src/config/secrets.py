from __future__ import annotations

"""Do not read secrets via st.secrets or os.getenv directly anywhere else. Always use get_secret()."""

import os
import tomllib
import logging
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

_ALIAS_MAP: dict[str, list[str]] = {
    "SUPABASE_URL": ["SUPABASE_URL", "supabase_url", "SUPABASE__URL"],
    "SUPABASE_ANON_KEY": ["SUPABASE_ANON_KEY", "SUPABASE_KEY", "supabase_anon_key", "supabase_key"],
    "SUPABASE_SERVICE_ROLE_KEY": ["SUPABASE_SERVICE_ROLE_KEY", "supabase_service_role_key"],
    "OPENAI_API_KEY": ["OPENAI_API_KEY", "openai_api_key"],
    "GEMINI_API_KEY": ["GEMINI_API_KEY", "google_api_key", "GOOGLE_API_KEY", "google_ai_studio_api_key"],
    "GEMINI_MODEL_TEXT": ["GEMINI_MODEL_TEXT", "gemini_model_text"],
    "GEMINI_MODEL_FAST": ["GEMINI_MODEL_FAST", "gemini_model_fast"],
    "GEMINI_IMAGE_MODEL": ["GEMINI_IMAGE_MODEL", "gemini_image_model", "GOOGLE_AI_STUDIO_IMAGE_MODEL", "IMAGEN_MODEL", "imagen_model"],
    "GEMINI_VIDEO_MODEL": ["GEMINI_VIDEO_MODEL", "gemini_video_model", "HF_GOOGLE_VIDEO_MODEL", "hf_google_video_model"],
    "HF_VIDEO_PROVIDER": ["HF_VIDEO_PROVIDER", "hf_video_provider"],
    "HF_GOOGLE_VIDEO_MODEL": ["HF_GOOGLE_VIDEO_MODEL", "hf_google_video_model"],
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
    "GEMINI_API_KEY": (
        ("google", "api_key"),
        ("google_ai_studio", "api_key"),
        ("api_keys", "gemini"),
    ),
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
_FAL_KEY_CANDIDATES: tuple[str, ...] = ("FAL_KEY", "fal_key", "FAL_API_KEY", "fal_api_key")
logger = logging.getLogger(__name__)


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


def safe_str(value: Any) -> str:
    """Normalize unknown input to a trimmed string, never returning None."""
    if value is None:
        return ""
    return str(value).strip()


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
        try:
            if key in container:
                return True, container[key]
        except Exception:
            return False, None
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


def _read_streamlit_secret(key: str) -> Any | None:
    secrets = _safe_streamlit_secrets()
    if secrets is None:
        return None

    try:
        found, raw_value = _read_key(secrets, key)
        if found:
            if isinstance(raw_value, str):
                value = _normalize(raw_value)
            else:
                value = raw_value
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


def _flatten_secret_nodes(container: Any) -> list[Any]:
    nodes: list[Any] = []
    stack: list[Any] = [container]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        ident = id(current)
        if ident in seen:
            continue
        seen.add(ident)
        nodes.append(current)

        keys: list[str] = []
        if isinstance(current, Mapping):
            keys = [str(k) for k in current.keys()]
        else:
            try:
                keys = [str(k) for k in current.keys()]  # type: ignore[attr-defined]
            except Exception:
                keys = []

        for key in keys:
            found, value = _read_key(current, key)
            if not found:
                continue
            if isinstance(value, Mapping):
                stack.append(value)
                continue
            try:
                value_keys = value.keys()  # type: ignore[attr-defined]
            except Exception:
                value_keys = None
            if value_keys is not None:
                stack.append(value)
    return nodes


def validate_fal_key(value: Any) -> bool:
    cleaned = _normalize(value)
    if not cleaned:
        return False
    if ":" not in cleaned:
        return False
    return 12 <= len(cleaned) <= 512


def get_fal_key() -> str:
    secrets = _safe_streamlit_secrets()
    if secrets is not None:
        # 1) Root-level keys first.
        for name in _FAL_KEY_CANDIDATES:
            found, raw = _read_key(secrets, name)
            candidate = _normalize(raw) if found else ""
            if validate_fal_key(candidate):
                os.environ["FAL_KEY"] = candidate
                return candidate

        # 2) Nested sections next (any depth).
        for node in _flatten_secret_nodes(secrets):
            if node is secrets:
                continue
            for name in _FAL_KEY_CANDIDATES:
                found, raw = _read_key(node, name)
                candidate = _normalize(raw) if found else ""
                if validate_fal_key(candidate):
                    os.environ["FAL_KEY"] = candidate
                    return candidate

    # 3) Environment variables in required priority order.
    for name in _FAL_KEY_CANDIDATES:
        candidate = _read_env(name) or ""
        if validate_fal_key(candidate):
            os.environ["FAL_KEY"] = candidate
            return candidate

    raise RuntimeError(
        "fal.ai API key not found. Checked Streamlit secrets and environment variables for: "
        "FAL_KEY, fal_key, FAL_API_KEY, fal_api_key."
    )


def bootstrap_api_keys() -> None:
    try:
        get_fal_key()
    except Exception:
        # fal.ai is optional in some flows; fail only when the provider is used.
        pass


def fal_key_debug_snapshot() -> dict[str, Any]:
    secrets = _safe_streamlit_secrets()
    secrets_presence: dict[str, bool] = {}
    env_presence: dict[str, bool] = {}
    try:
        nodes = _flatten_secret_nodes(secrets) if secrets is not None else []
    except Exception:
        nodes = []
        secrets = None

    for name in _FAL_KEY_CANDIDATES:
        in_secrets = False
        if secrets is not None:
            for node in nodes:
                found, raw = _read_key(node, name)
                if found and _normalize(raw):
                    in_secrets = True
                    break
        secrets_presence[name] = in_secrets
        env_presence[name] = bool(_read_env(name))

    resolved = ""
    try:
        resolved = get_fal_key()
    except Exception:
        resolved = ""

    return {
        "secrets_presence": secrets_presence,
        "env_presence": env_presence,
        "resolved_has_colon": ":" in resolved if resolved else False,
        "resolved_length": len(resolved),
    }


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
    canonical = str(key).upper()
    aliases = _aliases(str(key))

    # 1) Streamlit secrets (root aliases first)
    try:
        secrets = _safe_streamlit_secrets()
        if secrets is not None:
            for alias in aliases:
                found, raw = _read_key(secrets, alias)
                if found:
                    if isinstance(raw, str):
                        value = _normalize(raw)
                    else:
                        value = raw
                    if value:
                        return value
            for path in _NESTED_STREAMLIT_PATHS.get(canonical, ()):
                nested = _mapping_path_get(secrets, path)
                if nested:
                    return nested
    except Exception:
        logger.exception("get_secret failed for key '%s'; using default.", key)

    # 2) Environment variables by aliases
    for alias in aliases:
        value = _read_env(alias)
        if value:
            return value

    # 3) Direct TOML fallback (headless/cron mode)
    toml_data = _load_toml_secrets()
    for alias in aliases:
        raw = toml_data.get(alias)
        if raw is not None:
            if isinstance(raw, str):
                value = _normalize(raw)
            else:
                value = raw
            if value:
                return value
    for path in _NESTED_STREAMLIT_PATHS.get(canonical, ()):
        nested = _mapping_path_get(toml_data, path)
        if nested:
            return nested

    # 4) Default if not found or any error occurs
    return default


def safe_secret(*names: str, default: str = "") -> str:
    """Resolve the first available secret from candidate names, never raising."""
    for name in names:
        try:
            value = safe_str(get_secret(name, ""))
        except Exception:
            value = ""
        if value:
            return value
    return safe_str(default)


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
    try:
        return bool(get_fal_key())
    except Exception:
        return False


def get_openai_config() -> dict[str, str | None]:
    return {
        "api_key": get_secret("OPENAI_API_KEY"),
        "org": get_secret("OPENAI_ORG"),
        "project": get_secret("OPENAI_PROJECT"),
    }
