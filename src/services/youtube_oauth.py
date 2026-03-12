from __future__ import annotations

import secrets
from urllib.parse import urlencode

import streamlit as st


def build_youtube_auth_url() -> tuple[str, str]:
    state = secrets.token_urlsafe(32)

    params = {
        "client_id": st.secrets["google_oauth"]["client_id"],
        "redirect_uri": st.secrets["google_oauth"]["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(st.secrets["youtube"]["scopes"]),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return auth_url, state
