from __future__ import annotations

from dataclasses import dataclass, field

from src.trend_intelligence.adapters.interfaces import TopicAnalysisAdapter, TrendsSourceAdapter, YouTubeSourceAdapter
from src.trend_intelligence.adapters.mock_adapters import (
    MockTopicAnalysisAdapter,
    MockTrendsSourceAdapter,
    MockYouTubeSourceAdapter,
)
from src.trend_intelligence.adapters.schemas import TopicAnalysis, TrendingTopicSeed, VideoResult


@dataclass(frozen=True)
class PipelineTopicResult:
    seed: TrendingTopicSeed
    videos: tuple[VideoResult, ...]
    analysis: TopicAnalysis


@dataclass(frozen=True)
class FullScanPipelineResult:
    sources: tuple[str, ...]
    topics: tuple[PipelineTopicResult, ...] = field(default_factory=tuple)


class TrendIntelligencePipelineService:
    """Orchestrates modular source adapters and exposes normalized pipeline output."""

    def __init__(
        self,
        *,
        trends_adapter: TrendsSourceAdapter | None = None,
        youtube_adapter: YouTubeSourceAdapter | None = None,
        analysis_adapter: TopicAnalysisAdapter | None = None,
    ) -> None:
        self.trends_adapter = trends_adapter or MockTrendsSourceAdapter()
        self.youtube_adapter = youtube_adapter or MockYouTubeSourceAdapter()
        self.analysis_adapter = analysis_adapter or MockTopicAnalysisAdapter()

    def run_full_scan_pipeline(self, *, topic_limit: int = 5, videos_per_topic: int = 5) -> FullScanPipelineResult:
        seeds = self.trends_adapter.fetch_trending_topics(limit=topic_limit)

        merged_results: list[PipelineTopicResult] = []
        for seed in seeds:
            videos = self.youtube_adapter.search_topic_videos(seed.topic, limit=videos_per_topic)
            analysis = self.analysis_adapter.analyze_topic(seed.topic, videos)
            merged_results.append(PipelineTopicResult(seed=seed, videos=tuple(videos), analysis=analysis))

        source_names = (
            self.trends_adapter.source_name,
            self.youtube_adapter.source_name,
            self.analysis_adapter.source_name,
        )
        return FullScanPipelineResult(sources=source_names, topics=tuple(merged_results))
