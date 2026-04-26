from __future__ import annotations

import time

import pytest

from src.services import instagram_upload as mod


class _DummyResp:
    def __init__(self, ok: bool, payload: dict, text: str = "") -> None:
        self.ok = ok
        self._payload = payload
        self.text = text or str(payload)

    def json(self):
        return self._payload


def test_should_refresh_access_token_false_when_expiry_is_far(monkeypatch: pytest.MonkeyPatch):
    future = int(time.time()) + 20 * 86400
    monkeypatch.setattr(
        mod,
        "inspect_access_token",
        lambda **_kwargs: {"is_valid": True, "expires_at": future},
    )

    should_refresh, seconds_remaining = mod.should_refresh_access_token(window_days=7)

    assert should_refresh is False
    assert seconds_remaining is not None
    assert seconds_remaining > 7 * 86400


def test_refresh_access_token_skips_refresh_when_token_is_healthy(monkeypatch: pytest.MonkeyPatch):
    future = int(time.time()) + 20 * 86400
    monkeypatch.setattr(mod, "_get_access_token", lambda: "token-123")
    monkeypatch.setattr(mod, "get_secret", lambda name, *args, **kwargs: {"META_APP_ID": "app-1", "META_APP_SECRET": "secret-1"}.get(name, ""))
    monkeypatch.setattr(mod, "should_refresh_access_token", lambda **_kwargs: (False, future - int(time.time())))

    called = {"count": 0}

    def _no_request(*args, **kwargs):
        called["count"] += 1
        return _DummyResp(True, {})

    monkeypatch.setattr(mod.requests, "get", _no_request)

    token, expires_in = mod.refresh_access_token()

    assert token == "token-123"
    assert expires_in > 0
    assert called["count"] == 0


def test_inspect_access_token_raises_clear_message_for_app_mismatch(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mod, "_get_access_token", lambda: "token-123")
    monkeypatch.setattr(mod, "get_secret", lambda name, *args, **kwargs: {"META_APP_ID": "app-1", "META_APP_SECRET": "secret-1"}.get(name, ""))
    monkeypatch.setattr(
        mod.requests,
        "get",
        lambda *args, **kwargs: _DummyResp(False, {"error": {"message": "Error validating application."}}),
    )

    with pytest.raises(mod.InstagramUploadError, match="may not match the app"):
        mod.inspect_access_token()
