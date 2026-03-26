from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TimeframeOption = Literal["24h", "7d", "30d"]
ContentTypeOption = Literal["long-form", "shorts", "both"]
BrandFocusOption = Literal[
    "ancient history",
    "war history",
    "forgotten figures",
    "mysteries",
    "all",
]


@dataclass(frozen=True)
class TrendScanFilters:
    timeframe: TimeframeOption
    content_type: ContentTypeOption
    brand_focus: BrandFocusOption
    min_score: int


@dataclass(frozen=True)
class TopicScoreBreakdown:
    trend_momentum_score: int
    watch_time_potential_score: int
    clickability_score: int
    competition_gap_score: int
    brand_alignment_score: int


@dataclass(frozen=True)
class TopicInsight:
    reasoning: str
    content_angle_ideas: list[str]
    hook_ideas: list[str]
    thumbnail_ideas: list[str]


@dataclass(frozen=True)
class TopicResult:
    topic_title: str
    total_score: int
    score_breakdown: TopicScoreBreakdown
    insight: TopicInsight


@dataclass(frozen=True)
class ScriptBuilderPayload:
    topic_title: str
    why_may_be_trending: str
    preferred_content_angle: str
    selected_hook: str
    thumbnail_direction: str
    score_breakdown: TopicScoreBreakdown
