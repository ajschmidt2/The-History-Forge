"""Centralised secret / configuration helpers.

All other modules should import ``get_secret`` from here rather than
duplicating the lookup logic.
"""
from __future__ import annotations

import os


def _normalize(value: str) -> str:
    """Strip whitespace and surrounding quotes; reject known placeholder strings."""
    v = str(value or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}:
        v = v[1:-1].strip()
    low = v.lower()
    if low in {"none", "null", ""}:
        return ""
    # Reject any value that looks like an unfilled template placeholder.
    # Covers patterns like PASTE_KEY_HERE, YOUR_API_KEY_HERE, REPLACE_ME, etc.
    if low.startswith(("paste_", "paste-", "your_", "your-", "replace_me", "changeme", "xxx")):
        return ""
    if low.endswith(("_here", "-here", "_key_here", "_token_here", "_id_here")):
        return ""
    if low in {"paste_key_here", "your_api_key_here", "replace_me"}:
        return ""
    return v


def get_secret(name: str, default: str = "") -> str:
    """Return a secret value, searching Streamlit secrets then env vars.

    Checks ``name``, ``name.lower()``, and ``name.upper()`` in that order.
    """
    candidates = list(dict.fromkeys([name, name.lower(), name.upper()]))

    try:
        import streamlit as st  # type: ignore

        if hasattr(st, "secrets"):
            for key in candidates:
                if key in st.secrets:
                    v = _normalize(str(st.secrets[key]))
                    if v:
                        return v
    except Exception:
        pass

    for key in candidates:
        v = _normalize(os.getenv(key, ""))
        if v:
            return v

    return _normalize(default)
