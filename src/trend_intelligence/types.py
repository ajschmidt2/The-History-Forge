from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class TrendScanFilters:
    timeframe: Literal["24h", "7d", "30d"] = "7d"
    content_type: Literal["long-form", "shorts", "both"] = "both"
    brand_focus: str = "all"
    minimum_score: float = 0.0


@dataclass(frozen=True)
class RawTrendTopic:
    topic: str
    source: str
    observed_at: datetime
    signal_strength: float = 0.0
    growth_rate: float = 0.0
    regional_interest: float = 0.0


@dataclass(frozen=True)
class YouTubeVideoCandidate:
    video_id: str
    title: str
    channel_title: str
    views: int
    likes: int
    comments: int
    duration_seconds: int
    published_at: datetime


@dataclass(frozen=True)
class TopicScoreBreakdown:
    trend_momentum: float
    watch_time_potential: float
    clickability: float
    competition_gap: float
    brand_alignment: float
    overall: float


@dataclass(frozen=True)
class TopicInsight:
    summary: str
    why_now: str
    opportunities: tuple[str, ...] = field(default_factory=tuple)
    risks: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TopicResult:
    topic: str
    score: TopicScoreBreakdown
    insight: TopicInsight
    source: str
    sampled_videos: tuple[YouTubeVideoCandidate, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TrendScanRun:
    run_id: str
    started_at: datetime
    completed_at: datetime | None
    filters: TrendScanFilters
    total_topics_scanned: int
    results: tuple[TopicResult, ...] = field(default_factory=tuple)
    status: Literal["running", "completed", "failed"] = "running"
