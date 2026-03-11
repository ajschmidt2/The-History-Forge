from __future__ import annotations

import src.broll.service as service


def test_search_broll_skips_unconfigured_provider(monkeypatch):
    monkeypatch.setattr(service, "broll_provider_status", lambda: {"pexels": False, "pixabay": True})
    monkeypatch.setattr(service, "search_pixabay_videos", lambda *args, **kwargs: [])

    result = service.search_broll("roman empire", "16:9", ["pexels", "pixabay"], 3)

    assert result == []
    assert "Pexels API key not found in Streamlit secrets." in service.get_last_search_errors()


def test_search_broll_returns_first_success(monkeypatch):
    monkeypatch.setattr(service, "broll_provider_status", lambda: {"pexels": True, "pixabay": True})
    monkeypatch.setattr(service, "search_pexels_videos", lambda *args, **kwargs: [])

    class Obj:
        provider = "pixabay"

    monkeypatch.setattr(service, "search_pixabay_videos", lambda *args, **kwargs: [Obj()])

    result = service.search_broll("roman empire", "16:9", ["pexels", "pixabay"], 3)

    assert len(result) == 1
    assert result[0].provider == "pixabay"
