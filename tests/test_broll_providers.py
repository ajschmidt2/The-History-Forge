from __future__ import annotations

from types import SimpleNamespace

import src.broll.providers as providers


def test_search_pexels_uses_authorization_header(monkeypatch):
    monkeypatch.setattr(providers, "get_pexels_api_key", lambda: "pexels-key")

    captured = {}

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "videos": [
                    {
                        "id": 123,
                        "duration": 4,
                        "url": "https://pexels.com/video/123",
                        "video_files": [
                            {"file_type": "video/mp4", "link": "https://cdn/123.mp4", "width": 1280, "height": 720}
                        ],
                        "video_pictures": [{"picture": "https://img/123.jpg"}],
                    }
                ]
            }

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        captured["timeout"] = timeout
        return Resp()

    monkeypatch.setattr(providers.requests, "get", fake_get)

    results = providers.search_pexels_videos("rome", "9:16", per_page=2)

    assert captured["url"] == "https://api.pexels.com/videos/search"
    assert captured["headers"]["Authorization"] == "pexels-key"
    assert captured["params"]["orientation"] == "portrait"
    assert results and results[0].provider == "pexels"


def test_search_pixabay_uses_key_query_param(monkeypatch):
    monkeypatch.setattr(providers, "get_pixabay_api_key", lambda: "pixabay-key")

    captured = {}

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "hits": [
                    {
                        "id": 321,
                        "duration": 6,
                        "tags": "roman, empire",
                        "pageURL": "https://pixabay.com/videos/321/",
                        "user": "creator",
                        "videos": {
                            "medium": {"url": "https://cdn/321.mp4", "width": 720, "height": 1280},
                            "tiny": {"thumbnail": "https://img/321.jpg", "url": "https://cdn/321_tiny.mp4", "width": 180, "height": 320},
                        },
                    }
                ]
            }

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return Resp()

    monkeypatch.setattr(providers.requests, "get", fake_get)

    results = providers.search_pixabay_videos("rome", "9:16", per_page=1)

    assert captured["url"] == "https://pixabay.com/api/videos/"
    assert captured["params"]["key"] == "pixabay-key"
    assert "Authorization" not in captured["params"]
    assert results and results[0].orientation == "vertical"
