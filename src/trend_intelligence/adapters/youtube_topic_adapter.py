from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from googleapiclient.discovery import build  # type: ignore
from googleapiclient.errors import HttpError  # type: ignore

from src.config import get_secret
from src.trend_intelligence.adapters.interfaces import YouTubeSourceAdapter
from src.trend_intelligence.adapters.schemas import VideoResult
from src.trend_intelligence.adapters.mock_adapters import MockYouTubeSourceAdapter
from src.trend_intelligence.types import YouTubeVideoCandidate

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SearchVideoHit:
    video_id: str


@dataclass(frozen=True)
class _VideoMetadata:
    video_id: str
    title: str
    channel_title: str
    channel_id: str
    published_at: datetime
    duration_seconds: int
    view_count: int
    like_count: int
    comment_count: int


class YouTubeTopicSourceAdapter(YouTubeSourceAdapter):
    """YouTube Data API adapter for topic-level trend intelligence sampling."""

    source_name = "youtube_data_api"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        throttle_seconds: float = 0.15,
        fallback: YouTubeSourceAdapter | None = None,
    ) -> None:
        raw_api_key = api_key if api_key is not None else get_secret("YOUTUBE_API_KEY")
        if isinstance(raw_api_key, str):
            raw_api_key = raw_api_key.strip()
        self.api_key = raw_api_key or None
        self.enabled = bool(self.api_key)
        self.status_message = "" if self.enabled else "YouTube source disabled: missing YOUTUBE_API_KEY."
        self.throttle_seconds = max(0.0, throttle_seconds)
        self.fallback = fallback or MockYouTubeSourceAdapter()
        if not self.enabled:
            logger.warning("YOUTUBE_API_KEY is missing or empty; YouTube Trend adapter is disabled.")

    def search_topic_videos(self, topic: str, *, limit: int, content_type: str = "both") -> list[VideoResult]:
        safe_limit = max(0, int(limit))
        if safe_limit == 0:
            return []

        if not self.enabled:
            logger.info("YouTube Trend adapter is disabled; returning empty results.")
            return []

        try:
            client = self._client()
        except Exception:
            logger.exception("YouTube API client initialization failed; using fallback adapter.")
            return self.fallback.search_topic_videos(topic, limit=safe_limit, content_type=content_type)

        if client is None:
            return self.fallback.search_topic_videos(topic, limit=safe_limit, content_type=content_type)

        try:
            query, duration_filter, order = _build_youtube_query(topic=topic, content_type=content_type)
            search_hits = self._search_videos(
                client=client,
                topic=query,
                limit=safe_limit,
                video_duration=duration_filter,
                order=order,
            )
            if not search_hits:
                return []

            details = self._fetch_video_metadata(client=client, video_ids=[item.video_id for item in search_hits])
            if not details:
                return []

            channel_sizes = self._fetch_channel_sizes(
                client=client,
                channel_ids=[item.channel_id for item in details if item.channel_id],
            )
            normalized = [
                self._to_candidate(item=item, channel_sizes=channel_sizes)
                for item in details
            ]
            return [self._candidate_to_video_result(topic=topic, candidate=item) for item in normalized[:safe_limit]]
        except Exception:
            logger.exception(
                "YouTube topic lookup failed; using fallback adapter.",
                extra={"topic": topic, "limit": safe_limit, "source": self.source_name, "content_type": content_type},
            )
            return self.fallback.search_topic_videos(topic, limit=safe_limit, content_type=content_type)

    def _client(self):
        if not self.enabled:
            raise RuntimeError("YouTube API client unavailable: missing YOUTUBE_API_KEY.")
        try:
            return build("youtube", "v3", developerKey=self.api_key, cache_discovery=False)
        except Exception:
            logger.exception("Failed to initialize YouTube Data API client.")
            return None

    def _search_videos(
        self,
        *,
        client: Any,
        topic: str,
        limit: int,
        video_duration: str,
        order: str,
    ) -> list[_SearchVideoHit]:
        self._throttle()
        response = (
            client.search()
            .list(
                q=topic,
                part="snippet",
                type="video",
                maxResults=min(limit, 50),
                relevanceLanguage="en",
                order=order,
                videoDuration=video_duration,
            )
            .execute()
        )

        hits: list[_SearchVideoHit] = []
        for item in response.get("items", []):
            video_id = str(item.get("id", {}).get("videoId", "")).strip()
            if not video_id:
                continue
            hits.append(_SearchVideoHit(video_id=video_id))
        return hits

    def _fetch_video_metadata(self, *, client: Any, video_ids: list[str]) -> list[_VideoMetadata]:
        if not video_ids:
            return []

        self._throttle()
        response = (
            client.videos()
            .list(
                part="snippet,statistics,contentDetails",
                id=",".join(video_ids[:50]),
                maxResults=min(len(video_ids), 50),
            )
            .execute()
        )

        output: list[_VideoMetadata] = []
        for item in response.get("items", []):
            parsed = self._parse_video_metadata(item)
            if parsed is not None:
                output.append(parsed)
        return output

    def _fetch_channel_sizes(self, *, client: Any, channel_ids: list[str]) -> dict[str, int | None]:
        unique_ids = sorted({channel_id for channel_id in channel_ids if channel_id})
        if not unique_ids:
            return {}

        self._throttle()
        try:
            response = (
                client.channels()
                .list(
                    part="statistics",
                    id=",".join(unique_ids[:50]),
                    maxResults=min(len(unique_ids), 50),
                )
                .execute()
            )
        except HttpError:
            logger.exception("YouTube channels.list failed; channel sizes omitted.")
            return {}

        sizes: dict[str, int | None] = {}
        for item in response.get("items", []):
            channel_id = str(item.get("id", "")).strip()
            if not channel_id:
                continue
            stats = item.get("statistics", {})
            sizes[channel_id] = _safe_int(stats.get("subscriberCount"))
        return sizes

    def _parse_video_metadata(self, item: dict[str, Any]) -> _VideoMetadata | None:
        video_id = str(item.get("id", "")).strip()
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content_details = item.get("contentDetails", {})

        if not video_id:
            return None

        published_at = _parse_rfc3339(str(snippet.get("publishedAt", "")))
        return _VideoMetadata(
            video_id=video_id,
            title=str(snippet.get("title", "")).strip(),
            channel_title=str(snippet.get("channelTitle", "")).strip(),
            channel_id=str(snippet.get("channelId", "")).strip(),
            published_at=published_at,
            duration_seconds=_iso_duration_to_seconds(str(content_details.get("duration", ""))),
            view_count=_safe_int(stats.get("viewCount")) or 0,
            like_count=_safe_int(stats.get("likeCount")) or 0,
            comment_count=_safe_int(stats.get("commentCount")) or 0,
        )

    def _to_candidate(self, *, item: _VideoMetadata, channel_sizes: dict[str, int | None]) -> YouTubeVideoCandidate:
        # Channel subscriber count is fetched for ranking enrichments but not leaked in adapter response contract.
        _ = channel_sizes.get(item.channel_id)
        return YouTubeVideoCandidate(
            video_id=item.video_id,
            title=item.title,
            channel_title=item.channel_title,
            views=item.view_count,
            likes=item.like_count,
            comments=item.comment_count,
            duration_seconds=item.duration_seconds,
            published_at=item.published_at,
        )

    def _candidate_to_video_result(self, *, topic: str, candidate: YouTubeVideoCandidate) -> VideoResult:
        return VideoResult(
            topic=topic,
            video_id=candidate.video_id,
            title=candidate.title,
            channel_title=candidate.channel_title,
            view_count=candidate.views,
            like_count=candidate.likes,
            comment_count=candidate.comments,
            published_at=candidate.published_at.isoformat().replace("+00:00", "Z"),
            duration_minutes=round(candidate.duration_seconds / 60, 2),
            source=self.source_name,
        )

    def _throttle(self) -> None:
        if self.throttle_seconds > 0:
            time.sleep(self.throttle_seconds)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_rfc3339(value: str) -> datetime:
    if not value:
        return datetime.now(UTC)

    sanitized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(sanitized).astimezone(UTC)
    except ValueError:
        return datetime.now(UTC)


def _iso_duration_to_seconds(value: str) -> int:
    if not value.startswith("PT"):
        return 0

    total = 0
    number = ""
    for char in value[2:]:
        if char.isdigit():
            number += char
            continue

        if not number:
            continue

        amount = int(number)
        if char == "H":
            total += amount * 3600
        elif char == "M":
            total += amount * 60
        elif char == "S":
            total += amount
        number = ""

    return total


def _build_youtube_query(*, topic: str, content_type: str) -> tuple[str, str, str]:
    normalized = (content_type or "both").strip().lower()
    if normalized == "shorts":
        return (f"{topic} history shorts", "short", "relevance")
    if normalized == "long-form":
        return (f"{topic} history documentary", "long", "relevance")
    return (f"{topic} history documentary", "any", "relevance")
