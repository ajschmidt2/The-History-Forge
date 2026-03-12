from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import src.services.youtube_oauth as youtube_oauth


def test_build_youtube_auth_url_includes_expected_google_oauth_params(monkeypatch):
    monkeypatch.setattr(
        youtube_oauth,
        "st",
        SimpleNamespace(
            secrets={
                "google_oauth": {
                    "client_id": "test-client-id.apps.googleusercontent.com",
                    "redirect_uri": "https://example.com/oauth/callback",
                },
                "youtube": {"scopes": ["scope.one", "scope.two"]},
            }
        ),
    )

    auth_url, state = youtube_oauth.build_youtube_auth_url()

    parsed = urlparse(auth_url)
    params = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"
    assert parsed.path == "/o/oauth2/v2/auth"
    assert params["client_id"] == ["test-client-id.apps.googleusercontent.com"]
    assert params["redirect_uri"] == ["https://example.com/oauth/callback"]
    assert params["response_type"] == ["code"]
    assert params["scope"] == ["scope.one scope.two"]
    assert params["access_type"] == ["offline"]
    assert params["include_granted_scopes"] == ["true"]
    assert params["prompt"] == ["consent"]
    assert params["state"] == [state]
    assert state
