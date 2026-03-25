from __future__ import annotations

from abc import ABC, abstractmethod

from src.trend_intelligence.adapters.schemas import TopicAnalysis, TrendingTopicSeed, VideoResult


class TrendsSourceAdapter(ABC):
    """Adapter contract for trend seed providers (Google Trends, X, Reddit, etc.)."""

    source_name: str

    @abstractmethod
    def fetch_trending_topics(
        self,
        *,
        limit: int,
        timeframe: str = "7d",
    ) -> list[TrendingTopicSeed]:
        """Return normalized trend topic seeds."""
        raise NotImplementedError


class YouTubeSourceAdapter(ABC):
    """Adapter contract for YouTube topic lookups."""

    source_name: str

    @abstractmethod
    def search_topic_videos(self, topic: str, *, limit: int) -> list[VideoResult]:
        """Return normalized video result data for one topic."""
        raise NotImplementedError


class TopicAnalysisAdapter(ABC):
    """Adapter contract for analysis/enrichment providers (LLM, internal model, etc.)."""

    source_name: str

    @abstractmethod
    def analyze_topic(self, topic: str, videos: list[VideoResult]) -> TopicAnalysis:
        """Return normalized explanation, angles, hooks, and thumbnail ideas."""
        raise NotImplementedError
