from __future__ import annotations

"""Do not read secrets via st.secrets or os.getenv directly anywhere else. Always use get_secret()."""

import os
from collections.abc import Mapping
from typing import Any

_ALIAS_MAP: dict[str, list[str]] = {
    "SUPABASE_URL": ["SUPABASE_URL", "supabase_url", "SUPABASE__URL"],
    "SUPABASE_ANON_KEY": ["SUPABASE_ANON_KEY", "SUPABASE_KEY", "supabase_anon_key", "supabase_key"],
    "SUPABASE_SERVICE_ROLE_KEY": ["SUPABASE_SERVICE_ROLE_KEY", "supabase_service_role_key"],
    "OPENAI_API_KEY": ["OPENAI_API_KEY", "openai_api_key", "OPENAI_KEY", "openai_key", "api_key"],
    "IMAGES_BUCKET": ["IMAGES_BUCKET", "images_bucket"],
    "AUDIO_BUCKET": ["AUDIO_BUCKET", "audio_bucket"],
    "VIDEOS_BUCKET": ["VIDEOS_BUCKET", "videos_bucket"],
}

_NESTED_STREAMLIT_PATHS: dict[str, tuple[tuple[str, ...], ...]] = {
    "SUPABASE_URL": (("supabase", "url"),),
    "SUPABASE_ANON_KEY": (("supabase", "anon_key"), ("supabase", "key")),
    "SUPABASE_SERVICE_ROLE_KEY": (("supabase", "service_role_key"),),
    "OPENAI_API_KEY": (("openai", "api_key"),),
    "IMAGES_BUCKET": (("supabase", "images_bucket"), ("buckets", "images")),
    "AUDIO_BUCKET": (("supabase", "audio_bucket"), ("buckets", "audio")),
    "VIDEOS_BUCKET": (("supabase", "videos_bucket"), ("buckets", "videos")),
}

_PLACEHOLDER_VALUES = {"", "none", "null", "paste_key_here", "your_api_key_here", "replace_me"}


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
        if not secrets:
            return None
        if isinstance(secrets, Mapping) and len(secrets) == 0:
            return None
        return secrets
    except Exception:
        return None


def streamlit_secrets_detected() -> bool:
    return _safe_streamlit_secrets() is not None


def _mapping_path_get(mapping: Any, path: tuple[str, ...]) -> str:
    current = mapping
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return ""
        current = current[key]
    return _normalize(current)


def _read_streamlit_secret(key: str) -> str | None:
    secrets = _safe_streamlit_secrets()
    if secrets is None:
        return None

    try:
        if isinstance(secrets, Mapping) and key in secrets:
            value = _normalize(secrets[key])
        else:
            value = _normalize(secrets.get(key, ""))
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


def get_secret(name: str, default: str | None = None, required: bool = False) -> str | None:
    aliases = _aliases(name)

    for key in aliases:
        value = _read_streamlit_secret(key)
        if value:
            return value

    for key in aliases:
        value = _read_env(key)
        if value:
            return value

    fallback = _normalize(default)
    if fallback:
        return fallback

    if required:
        keys = ", ".join(aliases)
        raise RuntimeError(
            f"Missing required secret '{name}'. Expected one of: {keys}. "
            "Set it in Streamlit secrets (flat or nested TOML) or environment variables."
        )
    return None


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


def get_openai_config() -> dict[str, str | None]:
    return {
        "api_key": get_secret("OPENAI_API_KEY"),
        "org": get_secret("OPENAI_ORG"),
        "project": get_secret("OPENAI_PROJECT"),
    }
