from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from googleapiclient.discovery import build  # type: ignore

from src.config import get_secret
from src.trend_intelligence.adapters.base import VideoSourceAdapter
from src.trend_intelligence.models import VideoSignal


class YouTubeDataApiAdapter(VideoSourceAdapter):
    source_name = "youtube_data_api"

    def __init__(self) -> None:
        self.api_key = get_secret("YOUTUBE_API_KEY")

    def _client(self):
        if not self.api_key:
            return None
        return build("youtube", "v3", developerKey=self.api_key, cache_discovery=False)

    def search_videos(self, topic: str, *, limit: int) -> list[VideoSignal]:
        client = self._client()
        if client is None:
            return []

        search_resp = (
            client.search()
            .list(
                q=topic,
                part="snippet",
                type="video",
                maxResults=min(limit, 20),
                relevanceLanguage="en",
                order="viewCount",
                videoDuration="long",
            )
            .execute()
        )
        video_ids = [item["id"]["videoId"] for item in search_resp.get("items", []) if item.get("id", {}).get("videoId")]
        if not video_ids:
            return []

        video_resp = (
            client.videos()
            .list(part="snippet,statistics,contentDetails", id=",".join(video_ids))
            .execute()
        )

        signals: list[VideoSignal] = []
        for item in video_resp.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            duration_minutes = _iso_duration_minutes(item.get("contentDetails", {}).get("duration", ""))
            signals.append(
                VideoSignal(
                    topic=topic,
                    title=str(snippet.get("title", "")),
                    channel_title=str(snippet.get("channelTitle", "")),
                    view_count=int(stats.get("viewCount", 0) or 0),
                    like_count=int(stats.get("likeCount", 0) or 0),
                    comment_count=int(stats.get("commentCount", 0) or 0),
                    published_at=str(snippet.get("publishedAt", "")),
                    duration_minutes=duration_minutes,
                )
            )
        return signals


def _iso_duration_minutes(value: str) -> float:
    # Fast, no extra dependency parser for PT#H#M#S.
    if not value.startswith("PT"):
        return 0.0

    rest = value[2:]
    hours = _pull_unit(rest, "H")
    minutes = _pull_unit(rest, "M")
    seconds = _pull_unit(rest, "S")
    return round((hours * 60) + minutes + (seconds / 60), 2)


def _pull_unit(raw: str, unit: str) -> int:
    if unit not in raw:
        return 0
    prefix = raw.split(unit, 1)[0]
    digits = ""
    for c in reversed(prefix):
        if not c.isdigit():
            break
        digits = c + digits
    try:
        return int(digits or 0)
    except ValueError:
        return 0
