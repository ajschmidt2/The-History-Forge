from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Iterable

from src.trend_intelligence.adapters.interfaces import TopicAnalysisAdapter, TrendsSourceAdapter, YouTubeSourceAdapter
from src.trend_intelligence.adapters.mock_adapters import GoogleTrendsSeedsAdapter
from src.trend_intelligence.adapters.topic_analysis_adapter import OpenAITopicAnalysisAdapter
from src.trend_intelligence.adapters.youtube_topic_adapter import YouTubeTopicSourceAdapter
from src.trend_intelligence.adapters.schemas import TopicAnalysis, TrendingTopicSeed, VideoResult
from src.trend_intelligence.scoring import (
    build_score_breakdown,
    scoreBrandAlignment,
    scoreClickability,
    scoreCompetitionGap,
    scoreTrendMomentum,
    scoreWatchTimePotential,
)
from src.trend_intelligence.types import (
    RawTrendTopic,
    TopicInsight,
    TopicResult,
    TrendScanFilters,
    YouTubeVideoCandidate,
)


@dataclass(frozen=True)
class PipelineTopicResult:
    seed: TrendingTopicSeed
    videos: tuple[VideoResult, ...]
    analysis: TopicAnalysis


@dataclass(frozen=True)
class FullScanPipelineResult:
    sources: tuple[str, ...]
    topics: tuple[PipelineTopicResult, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ScanWarning:
    source: str
    message: str


@dataclass(frozen=True)
class TrendScanExecutionResult:
    scanned_at: datetime
    filters: TrendScanFilters
    topics: tuple[TopicResult, ...] = field(default_factory=tuple)
    warnings: tuple[ScanWarning, ...] = field(default_factory=tuple)


class TrendIntelligencePipelineService:
    """Orchestrates modular adapters and exposes deterministic scan outputs."""

    def __init__(
        self,
        *,
        trends_adapter: TrendsSourceAdapter | None = None,
        youtube_adapter: YouTubeSourceAdapter | None = None,
        analysis_adapter: TopicAnalysisAdapter | None = None,
    ) -> None:
        self.trends_adapter = trends_adapter or GoogleTrendsSeedsAdapter()
        self.youtube_adapter = youtube_adapter or YouTubeTopicSourceAdapter()
        self.analysis_adapter = analysis_adapter or OpenAITopicAnalysisAdapter()

    def run_full_scan_pipeline(
        self,
        *,
        topic_limit: int = 5,
        videos_per_topic: int = 5,
        timeframe: str = "7d",
    ) -> FullScanPipelineResult:
        seeds = self.trends_adapter.fetch_trending_topics(limit=topic_limit, timeframe=timeframe)

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

    def run_trend_intelligence_scan(
        self,
        filters: TrendScanFilters,
        *,
        topic_limit: int = 8,
        videos_per_topic: int = 10,
        max_workers: int = 4,
    ) -> list[TopicResult]:
        return list(
            self.run_trend_intelligence_scan_with_status(
                filters,
                topic_limit=topic_limit,
                videos_per_topic=videos_per_topic,
                max_workers=max_workers,
            ).topics
        )

    def run_trend_intelligence_scan_with_status(
        self,
        filters: TrendScanFilters,
        *,
        topic_limit: int = 8,
        videos_per_topic: int = 10,
        max_workers: int = 4,
    ) -> TrendScanExecutionResult:
        """
        Execute the full Trend Intelligence scan pipeline and return ranked topic results.

        The returned list is deterministic: ties are broken by topic title (ascending).
        """
        seeds = self.trends_adapter.fetch_trending_topics(limit=topic_limit, timeframe=filters.timeframe)
        if not seeds:
            return TrendScanExecutionResult(scanned_at=datetime.now(UTC), filters=filters)

        worker_count = max(1, min(max_workers, len(seeds)))
        ranked_results: list[TopicResult] = []
        warnings: list[ScanWarning] = []

        # NOTE: ThreadPoolExecutor is sufficient here because adapter calls are primarily I/O-bound.
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_seed = {
                executor.submit(self._build_topic_result, seed, filters, videos_per_topic): seed for seed in seeds
            }
            for future in as_completed(future_to_seed):
                seed = future_to_seed[future]
                try:
                    ranked_results.append(future.result())
                except Exception as exc:
                    warnings.append(
                        ScanWarning(
                            source=f"topic:{seed.source}",
                            message=f"Failed to analyze '{seed.topic}': {exc}",
                        )
                    )

        filtered_results = [result for result in ranked_results if result.score.overall >= filters.minimum_score]
        filtered_results.sort(key=lambda result: (-result.score.overall, result.topic.lower()))
        return TrendScanExecutionResult(
            scanned_at=datetime.now(UTC),
            filters=filters,
            topics=tuple(filtered_results),
            warnings=tuple(warnings),
        )

    def _build_topic_result(self, seed: TrendingTopicSeed, filters: TrendScanFilters, videos_per_topic: int) -> TopicResult:
        videos = self.youtube_adapter.search_topic_videos(seed.topic, limit=videos_per_topic)
        analysis = self.analysis_adapter.analyze_topic(seed.topic, videos)

        video_candidates = tuple(self._to_video_candidate(video) for video in videos)
        raw_topic = self._to_raw_topic(seed)

        score = build_score_breakdown(
            trend_momentum=scoreTrendMomentum(raw_topic),
            watch_time_potential=scoreWatchTimePotential(list(video_candidates)),
            clickability=scoreClickability(seed.topic, list(video_candidates)),
            competition_gap=scoreCompetitionGap(list(video_candidates)),
            brand_alignment=scoreBrandAlignment(seed.topic, filters.brand_focus),
        )

        insight = TopicInsight(
            # TODO: Replace this deterministic synthesis with deeper LLM narrative reasoning.
            summary=f"{seed.topic} scored {score.overall:.2f} with momentum from {seed.source} and {len(video_candidates)} sampled videos.",
            why_now=analysis.explanation or seed.reason,
            opportunities=tuple(self._build_content_angles(seed.topic, analysis.angles, filters.content_type)),
            risks=tuple(self._build_hooks_and_thumbnails(analysis.hooks, analysis.thumbnail_ideas)),
        )

        source_metadata = self._build_source_metadata(
            trend_source=seed.source,
            analysis_source=analysis.source,
            video_candidates=video_candidates,
        )

        return TopicResult(
            topic=seed.topic,
            score=score,
            insight=insight,
            source=source_metadata,
            sampled_videos=video_candidates,
        )

    def _to_raw_topic(self, seed: TrendingTopicSeed) -> RawTrendTopic:
        raw = seed.raw or {}
        raw_topic = raw.get("raw_topic", {}) if isinstance(raw, dict) else {}

        observed_at = _safe_parse_datetime(raw_topic.get("observed_at"))
        return RawTrendTopic(
            topic=seed.topic,
            source=seed.source,
            observed_at=observed_at,
            signal_strength=float(raw_topic.get("signal_strength", seed.momentum)),
            growth_rate=float(raw_topic.get("growth_rate", 0.0)),
            regional_interest=float(raw_topic.get("regional_interest", 0.0)),
        )

    @staticmethod
    def _to_video_candidate(video: VideoResult) -> YouTubeVideoCandidate:
        return YouTubeVideoCandidate(
            video_id=video.video_id,
            title=video.title,
            channel_title=video.channel_title,
            views=video.view_count,
            likes=video.like_count,
            comments=video.comment_count,
            duration_seconds=max(0, int(round(video.duration_minutes * 60))),
            published_at=_safe_parse_datetime(video.published_at),
        )

    @staticmethod
    def _build_content_angles(topic: str, adapter_angles: tuple[str, ...], content_type: str) -> Iterable[str]:
        if adapter_angles:
            yield from adapter_angles

        if content_type in {"long-form", "both"}:
            yield f"Long-form deep dive: the overlooked turning points in {topic}"
        if content_type in {"shorts", "both"}:
            yield f"Short-form rapid timeline: {topic} in 60 seconds"

    @staticmethod
    def _build_hooks_and_thumbnails(hooks: tuple[str, ...], thumbnails: tuple[str, ...]) -> Iterable[str]:
        yield from hooks
        yield from (f"Thumbnail idea: {idea}" for idea in thumbnails)

    @staticmethod
    def _build_source_metadata(
        *,
        trend_source: str,
        analysis_source: str,
        video_candidates: tuple[YouTubeVideoCandidate, ...],
    ) -> str:
        video_sources = ",".join(sorted({"youtube" for _ in video_candidates} or {"youtube"}))
        return f"trend={trend_source};videos={video_sources};analysis={analysis_source}"


def _safe_parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    sanitized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(sanitized)
    except ValueError:
        return datetime.now(UTC)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
