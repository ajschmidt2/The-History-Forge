from __future__ import annotations

from src.trend_intelligence.adapters.base import TrendSourceAdapter, VideoSourceAdapter
from src.trend_intelligence.models import TrendSignal, VideoSignal


class MockTrendAdapter(TrendSourceAdapter):
    """Fallback adapter for local/dev when external trend sources are unavailable."""

    source_name = "mock_trend_seed"

    def fetch_topics(self, *, limit: int) -> list[TrendSignal]:
        seeds = [
            "The Fall of Constantinople",
            "The Suez Crisis",
            "What caused the Bronze Age collapse",
            "The 1918 Flu and modern pandemics",
            "The Opium Wars explained",
        ]
        return [
            TrendSignal(
                topic=t,
                source=self.source_name,
                momentum=max(0.45, 0.82 - (i * 0.08)),
                reason="Seeded fallback topic for local analysis.",
            )
            for i, t in enumerate(seeds[:limit])
        ]


class PlaceholderVideoAdapter(VideoSourceAdapter):
    """Future extension point (Perplexity, internal analytics, etc.)."""

    source_name = "placeholder_video_source"

    def search_videos(self, topic: str, *, limit: int) -> list[VideoSignal]:
        return []
