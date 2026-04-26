from __future__ import annotations

import src.trend_intelligence.adapters.youtube_topic_adapter as adapter_mod
from src.trend_intelligence.adapters.schemas import VideoResult
from src.trend_intelligence.adapters.youtube_topic_adapter import YouTubeTopicSourceAdapter


class _Request:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _SearchResource:
    def __init__(self, payload):
        self.payload = payload

    def list(self, **_kwargs):
        return _Request(self.payload)


class _VideosResource:
    def __init__(self, payload):
        self.payload = payload

    def list(self, **_kwargs):
        return _Request(self.payload)


class _ChannelsResource:
    def __init__(self, payload):
        self.payload = payload

    def list(self, **_kwargs):
        return _Request(self.payload)


class _YouTubeClient:
    def __init__(self, *, search_payload, videos_payload, channels_payload):
        self._search_payload = search_payload
        self._videos_payload = videos_payload
        self._channels_payload = channels_payload

    def search(self):
        return _SearchResource(self._search_payload)

    def videos(self):
        return _VideosResource(self._videos_payload)

    def channels(self):
        return _ChannelsResource(self._channels_payload)


def test_youtube_topic_adapter_normalizes_results_and_applies_limit(monkeypatch):
    adapter = YouTubeTopicSourceAdapter(api_key="test-key", throttle_seconds=0)
    fake_client = _YouTubeClient(
        search_payload={
            "items": [
                {"id": {"videoId": "abc"}},
                {"id": {"videoId": "def"}},
            ]
        },
        videos_payload={
            "items": [
                {
                    "id": "abc",
                    "snippet": {
                        "title": "Why Rome Fell",
                        "channelTitle": "History Lab",
                        "channelId": "chan-1",
                        "publishedAt": "2026-03-20T00:00:00Z",
                    },
                    "statistics": {"viewCount": "120000", "likeCount": "5000", "commentCount": "210"},
                    "contentDetails": {"duration": "PT12M30S"},
                },
                {
                    "id": "def",
                    "snippet": {
                        "title": "Bronze Age Collapse",
                        "channelTitle": "Past Uncovered",
                        "channelId": "chan-2",
                        "publishedAt": "2026-03-18T00:00:00Z",
                    },
                    "statistics": {"viewCount": "42000", "likeCount": "2100", "commentCount": "88"},
                    "contentDetails": {"duration": "PT8M"},
                },
            ]
        },
        channels_payload={"items": [{"id": "chan-1", "statistics": {"subscriberCount": "1000000"}}]},
    )
    monkeypatch.setattr(adapter, "_client", lambda: fake_client)

    results = adapter.search_topic_videos("Rome", limit=1)

    assert len(results) == 1
    assert isinstance(results[0], VideoResult)
    assert results[0].video_id == "abc"
    assert results[0].duration_minutes == 12.5
    assert results[0].view_count == 120000
    assert results[0].source == "youtube_data_api"


def test_youtube_topic_adapter_falls_back_when_api_errors(monkeypatch):
    class _Fallback:
        source_name = "fallback"

        def search_topic_videos(self, topic: str, *, limit: int):
            return [
                VideoResult(
                    topic=topic,
                    video_id="fallback-1",
                    title="Fallback",
                    channel_title="Fallback Channel",
                    view_count=1,
                    like_count=0,
                    comment_count=0,
                    published_at="2026-03-01T00:00:00Z",
                    duration_minutes=1.0,
                    source="fallback",
                )
            ][:limit]

    adapter = YouTubeTopicSourceAdapter(api_key="test-key", throttle_seconds=0, fallback=_Fallback())
    monkeypatch.setattr(adapter, "_client", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    results = adapter.search_topic_videos("Rome", limit=1)

    assert len(results) == 1
    assert results[0].source == "fallback"


def test_youtube_topic_adapter_init_with_valid_string_key():
    adapter = YouTubeTopicSourceAdapter(api_key="  test-key  ", throttle_seconds=0)

    assert adapter.api_key == "test-key"
    assert adapter.enabled is True
    assert adapter.status_message == ""


def test_youtube_topic_adapter_init_with_none_key(monkeypatch):
    monkeypatch.setattr(adapter_mod, "get_secret", lambda *_args, **_kwargs: None)

    adapter = YouTubeTopicSourceAdapter(api_key=None, throttle_seconds=0)

    assert adapter.api_key is None
    assert adapter.enabled is False
    assert "disabled" in adapter.status_message.lower()
    assert adapter.search_topic_videos("rome", limit=3) == []


def test_youtube_topic_adapter_init_with_empty_key():
    adapter = YouTubeTopicSourceAdapter(api_key="   ", throttle_seconds=0)

    assert adapter.api_key is None
    assert adapter.enabled is False
