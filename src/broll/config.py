from __future__ import annotations

import os
from typing import Any


def _normalize(value: Any) -> str:
    cleaned = str(value or "").strip()
    return cleaned


def _from_streamlit_secrets(name: str) -> str:
    try:
        import streamlit as st  # type: ignore

        value = st.secrets[name]
        return _normalize(value)
    except Exception:
        return ""


def _read_secret(primary: str, aliases: tuple[str, ...] = ()) -> str:
    keys = (primary, *aliases)
    for key in keys:
        value = _from_streamlit_secrets(key)
        if value:
            return value
    for key in keys:
        value = _normalize(os.environ.get(key, ""))
        if value:
            return value
    return ""


def get_pexels_api_key() -> str:
    return _read_secret("PEXELS_API_KEY", aliases=("pexels_api_key",))


def get_pixabay_api_key() -> str:
    return _read_secret("PIXABAY_API_KEY", aliases=("pixabay_api_key",))


def broll_provider_status() -> dict[str, bool]:
    return {
        "pexels": bool(get_pexels_api_key()),
        "pixabay": bool(get_pixabay_api_key()),
    }
