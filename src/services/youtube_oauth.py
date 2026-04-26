from __future__ import annotations

import secrets
from urllib.parse import urlparse, urlunparse, urlencode

import streamlit as st


def resolve_youtube_redirect_uri() -> str:
    raw = str(st.secrets["google_oauth"]["redirect_uri"]).strip()
    if not raw:
        return raw

    parsed = urlparse(raw)
    normalized_path = parsed.path or "/"
    if normalized_path.rstrip("/").endswith("/oauth/callback"):
        normalized_path = normalized_path[: -len("/oauth/callback")] or "/"
    if not normalized_path:
        normalized_path = "/"

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            normalized_path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def build_youtube_auth_url() -> tuple[str, str]:
    state = secrets.token_urlsafe(32)

    params = {
        "client_id": st.secrets["google_oauth"]["client_id"],
        "redirect_uri": resolve_youtube_redirect_uri(),
        "response_type": "code",
        "scope": " ".join(st.secrets["youtube"]["scopes"]),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return auth_url, state
