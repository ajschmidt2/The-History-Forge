from __future__ import annotations

import src.broll.config as broll_config


def test_get_pexels_api_key_uses_central_secret_loader(monkeypatch):
    monkeypatch.setattr(
        broll_config,
        "get_secret",
        lambda key, default="": "pexels-live-key" if key == "PEXELS_API_KEY" else "",
    )

    assert broll_config.get_pexels_api_key() == "pexels-live-key"


def test_get_pixabay_api_key_checks_alias_when_primary_missing(monkeypatch):
    def fake_get_secret(key, default=""):
        if key == "PIXABAY_API_KEY":
            return ""
        if key == "pixabay_api_key":
            return "pixabay-alias-key"
        return ""

    monkeypatch.setattr(broll_config, "get_secret", fake_get_secret)

    assert broll_config.get_pixabay_api_key() == "pixabay-alias-key"


def test_broll_provider_status_reflects_resolved_keys(monkeypatch):
    monkeypatch.setattr(broll_config, "get_pexels_api_key", lambda: "")
    monkeypatch.setattr(broll_config, "get_pixabay_api_key", lambda: "pixabay-key")

    assert broll_config.broll_provider_status() == {"pexels": False, "pixabay": True}
