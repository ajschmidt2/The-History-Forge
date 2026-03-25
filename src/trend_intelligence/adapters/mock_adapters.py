from __future__ import annotations

from src.config import get_secret
from src.trend_intelligence.adapters.interfaces import (
    TopicAnalysisAdapter,
    TrendsSourceAdapter,
    YouTubeSourceAdapter,
)
from src.trend_intelligence.adapters.schemas import TopicAnalysis, TrendingTopicSeed, VideoResult


class MockTrendsSourceAdapter(TrendsSourceAdapter):
    source_name = "mock_trends"

    def __init__(self, api_key: str | None = None) -> None:
        # Allows transparent swap to a live trends client later.
        self.api_key = api_key or get_secret("GOOGLE_TRENDS_API_KEY")

    def fetch_trending_topics(self, *, limit: int) -> list[TrendingTopicSeed]:
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
                reason="Mock trend seed for adapter pipeline scaffolding.",
                raw={"rank": idx + 1, "api_key_configured": bool(self.api_key)},
            )
            for idx, topic in enumerate(seeds[:limit])
        ]


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


class MockTopicAnalysisAdapter(TopicAnalysisAdapter):
    source_name = "mock_analysis"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or get_secret("OPENAI_API_KEY")

    def analyze_topic(self, topic: str, videos: list[VideoResult]) -> TopicAnalysis:
        video_count = len(videos)
        top_channel = videos[0].channel_title if videos else "N/A"
        return TopicAnalysis(
            topic=topic,
            explanation=(
                f"{topic} is showing durable audience interest across {video_count} related videos. "
                f"Top observed channel pattern: {top_channel}."
            ),
            angles=(
                f"What most people miss about {topic}",
                f"A timeline-first explainer of {topic}",
                f"How {topic} still impacts current geopolitics",
            ),
            hooks=(
                f"You were probably taught {topic} backwards.",
                f"This one decision changed {topic} forever.",
                f"The hidden catalyst behind {topic} nobody mentions.",
            ),
            thumbnail_ideas=(
                f"Split-era map + bold '{topic}' text",
                "Leader close-up + red arrow to turning-point date",
                "Before/after timeline bar with dramatic contrast",
            ),
            source=self.source_name,
        )
