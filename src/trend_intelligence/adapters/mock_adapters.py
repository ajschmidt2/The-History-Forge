from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import requests

from src.config import get_secret
from src.trend_intelligence.adapters.interfaces import (
    TopicAnalysisAdapter,
    TrendsSourceAdapter,
    YouTubeSourceAdapter,
)
from src.trend_intelligence.adapters.schemas import TopicAnalysis, TrendingTopicSeed, VideoResult
from src.trend_intelligence.adapters.topic_analysis_adapter import DeterministicTopicAnalysisAdapter
from src.trend_intelligence.types import RawTrendTopic

logger = logging.getLogger(__name__)


class MockTrendsSourceAdapter(TrendsSourceAdapter):
    source_name = "mock_trends"

    def __init__(self, api_key: str | None = None) -> None:
        # Allows transparent swap to a live trends client later.
        self.api_key = api_key or get_secret("GOOGLE_TRENDS_API_KEY")

    def fetch_trending_topics(self, *, limit: int, timeframe: str = "7d") -> list[TrendingTopicSeed]:
        seeds = [
            "The Fall of Constantinople",
            "The Suez Crisis",
            "What caused the Bronze Age collapse",
            "The 1918 Flu and modern pandemics",
            "The Opium Wars explained",
        ]
        return [
            TrendingTopicSeed(
                topic=topic,
                source=self.source_name,
                momentum=max(0.45, 0.82 - (idx * 0.08)),
                reason=f"Mock trend seed for adapter pipeline scaffolding (timeframe={timeframe}).",
                raw={
                    "rank": idx + 1,
                    "api_key_configured": bool(self.api_key),
                    "timeframe": timeframe,
                    "trend_direction": "up",
                    "breakout": False,
                    "related_queries": [],
                    "related_topics": [],
                    "raw_topic": {
                        "topic": topic,
                        "source": self.source_name,
                        "signal_strength": max(0.45, 0.82 - (idx * 0.08)),
                        "growth_rate": 0.35,
                        "regional_interest": 0.4,
                    },
                },
            )
            for idx, topic in enumerate(seeds[:limit])
        ]


class GoogleTrendsSeedsAdapter(TrendsSourceAdapter):
    """
    Google Trends RSS adapter with resilient fallback.

    This uses the public Google daily-trending RSS feed because it is the most
    deployment-friendly option currently available in this codebase (no browser
    automation, no scraping session cookies, no unofficial pytrends dependency).
    """

    source_name = "google_trends_rss"

    def __init__(
        self,
        *,
        geo: str | None = None,
        fallback: TrendsSourceAdapter | None = None,
    ) -> None:
        self.geo = geo or get_secret("GOOGLE_TRENDS_GEO", "US")
        self.fallback = fallback or MockTrendsSourceAdapter()

    def fetch_trending_topics(self, *, limit: int, timeframe: str = "7d") -> list[TrendingTopicSeed]:
        try:
            return self._fetch_from_google_rss(limit=limit, timeframe=timeframe)
        except Exception:
            logger.exception(
                "Google Trends RSS failed. Falling back to mock trend seeds.",
                extra={"source": self.source_name, "geo": self.geo, "timeframe": timeframe, "limit": limit},
            )
            return self.fallback.fetch_trending_topics(limit=limit, timeframe=timeframe)

    def _fetch_from_google_rss(self, *, limit: int, timeframe: str) -> list[TrendingTopicSeed]:
        url = f"https://trends.google.com/trending/rss?geo={self.geo}"
        response = requests.get(url, timeout=15)
        response.raise_for_status()

        root = ET.fromstring(response.text)
        channel = root.find("channel")
        if channel is None:
            return []

        topics: list[TrendingTopicSeed] = []
        ns = {"ht": "https://trends.google.com/trending/rss"}
        cutoff = _timeframe_cutoff(timeframe)
        for item in channel.findall("item"):
            topic = (item.findtext("title") or "").strip()
            if not topic:
                continue

            observed_at = _parse_rfc822(item.findtext("pubDate")) or datetime.now(UTC)
            if cutoff and observed_at < cutoff:
                continue

            approx_traffic = (item.findtext("ht:approx_traffic", "", ns) or "").strip()
            news_titles = [
                (node.text or "").strip()
                for node in item.findall("ht:news_item/ht:news_item_title", ns)
                if (node.text or "").strip()
            ]

            signal_strength, growth_rate, breakout = _traffic_to_signal(approx_traffic)
            raw_topic = RawTrendTopic(
                topic=topic,
                source=self.source_name,
                observed_at=observed_at,
                signal_strength=signal_strength,
                growth_rate=growth_rate,
                regional_interest=0.0,
            )

            topics.append(
                TrendingTopicSeed(
                    topic=topic,
                    source=self.source_name,
                    momentum=signal_strength,
                    reason=f"Google Trends RSS traffic signal: {approx_traffic or 'rising query'}",
                    raw={
                        "timeframe": timeframe,
                        "geo": self.geo,
                        "approx_traffic": approx_traffic,
                        "trend_direction": "up",
                        "breakout": breakout,
                        "related_queries": news_titles[:5],
                        "related_topics": [],
                        "raw_topic": {
                            "topic": raw_topic.topic,
                            "source": raw_topic.source,
                            "observed_at": raw_topic.observed_at.isoformat(),
                            "signal_strength": raw_topic.signal_strength,
                            "growth_rate": raw_topic.growth_rate,
                            "regional_interest": raw_topic.regional_interest,
                        },
                    },
                )
            )
            if len(topics) >= limit:
                break

        return topics


class MockYouTubeSourceAdapter(YouTubeSourceAdapter):
    source_name = "mock_youtube"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or get_secret("YOUTUBE_API_KEY")

    def search_topic_videos(self, topic: str, *, limit: int) -> list[VideoResult]:
        base_title = topic.split(":")[0].strip()
        return [
            VideoResult(
                topic=topic,
                video_id=f"mock-{idx + 1}",
                title=f"{base_title}: Documentary Breakdown #{idx + 1}",
                channel_title="History Forge Labs",
                view_count=250_000 - (idx * 22_000),
                like_count=13_500 - (idx * 800),
                comment_count=1_250 - (idx * 90),
                published_at="2026-02-01T00:00:00Z",
                duration_minutes=18.0 + idx,
                source=self.source_name,
            )
            for idx in range(max(0, limit))
        ]


class MockTopicAnalysisAdapter(DeterministicTopicAnalysisAdapter):
    """Back-compat alias for deterministic analysis in tests and local development."""

    source_name = "mock_analysis"


def _traffic_to_signal(approx_traffic: str) -> tuple[float, float, bool]:
    """
    Map RSS traffic labels (`20K+`, `1M+`, etc.) to normalized momentum metadata.
    """
    label = approx_traffic.strip().upper()
    if "M+" in label:
        return 0.95, 0.9, True
    if "K+" in label:
        return 0.78, 0.65, False
    return 0.62, 0.45, False


def _parse_rfc822(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value.strip(), "%a, %d %b %Y %H:%M:%S %z")
        return parsed.astimezone(UTC)
    except ValueError:
        return None


def _timeframe_cutoff(timeframe: str) -> datetime | None:
    now = datetime.now(UTC)
    mapping = {
        "24h": timedelta(days=1),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    delta = mapping.get(timeframe)
    if delta is None:
        return None
    return now - delta
