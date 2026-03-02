"""Centralised secret / configuration helpers.

All other modules should import ``get_secret`` from here rather than
duplicating the lookup logic.
"""
from __future__ import annotations

import os
from collections.abc import Mapping


def _normalize(value: str) -> str:
    """Strip whitespace and surrounding quotes; reject known placeholder strings."""
    v = str(value or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}:
        v = v[1:-1].strip()
    if v.lower() in {"paste_key_here", "your_api_key_here", "replace_me", "none", "null", "", "sk-...", "your-api-key"}:
        return ""
    if v.lower().startswith("paste") or v.lower().startswith("your_"):
        return ""
    return v


def get_secret(name: str, default: str = "") -> str:
    """Return a secret value, searching Streamlit secrets then env vars.

    Checks ``name``, ``name.lower()``, and ``name.upper()`` in that order.
    """
    candidates = list(dict.fromkeys([name, name.lower(), name.upper()]))

    lowered_name = name.lower()
    is_openai_key_lookup = (
        "openai" in lowered_name
        and ("key" in lowered_name or "token" in lowered_name)
        and "model" not in lowered_name
    )
    if is_openai_key_lookup:
        candidates.extend(["OPENAI_API_KEY", "openai_api_key", "OPENAI_KEY", "openai_key", "api_key"])
        candidates = list(dict.fromkeys(candidates))

    def _secret_from_mapping(mapping: Mapping[str, object], path: tuple[str, ...]) -> str:
        current: object = mapping
        for key in path:
            if not isinstance(current, Mapping) or key not in current:
                return ""
            current = current[key]
        return _normalize(str(current))

    try:
        import streamlit as st  # type: ignore

        if hasattr(st, "secrets"):
            for key in candidates:
                if key in st.secrets:
                    v = _normalize(str(st.secrets[key]))
                    if v:
                        return v

            if is_openai_key_lookup:
                nested_paths = [
                    ("openai", "api_key"),
                    ("openai", "OPENAI_API_KEY"),
                    ("OPENAI", "api_key"),
                    ("OPENAI", "API_KEY"),
                    ("providers", "openai", "api_key"),
                ]
                for path in nested_paths:
                    v = _secret_from_mapping(st.secrets, path)
                    if v:
                        return v
    except Exception:
        pass

    for key in candidates:
        v = _normalize(os.getenv(key, ""))
        if v:
            return v

    return _normalize(default)
