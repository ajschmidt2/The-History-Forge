from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TrendSignal:
    topic: str
    source: str
    momentum: float
    reason: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class VideoSignal:
    topic: str
    title: str
    channel_title: str
    view_count: int
    like_count: int
    comment_count: int
    published_at: str
    duration_minutes: float
    source: str = "youtube"


@dataclass
class TopicScore:
    momentum: float
    watch_time: float
    clickability: float
    competition_gap: float
    brand_alignment: float

    @property
    def total(self) -> float:
        return round(
            (self.momentum * 0.25)
            + (self.watch_time * 0.20)
            + (self.clickability * 0.20)
            + (self.competition_gap * 0.15)
            + (self.brand_alignment * 0.20),
            2,
        )


@dataclass
class RankedTopic:
    title: str
    score: TopicScore
    why_trending: str
    content_angles: list[str]
    hooks: list[str]
    thumbnail_ideas: list[str]
    trend_source: str
    youtube_video_count: int

    def as_db_payload(self, scan_id: str) -> dict[str, Any]:
        return {
            "scan_id": scan_id,
            "topic_title": self.title,
            "total_score": self.score.total,
            "momentum_score": self.score.momentum,
            "watch_time_score": self.score.watch_time,
            "clickability_score": self.score.clickability,
            "competition_gap_score": self.score.competition_gap,
            "brand_alignment_score": self.score.brand_alignment,
            "why_trending": self.why_trending,
            "content_angles": self.content_angles,
            "suggested_hooks": self.hooks,
            "thumbnail_ideas": self.thumbnail_ideas,
            "trend_source": self.trend_source,
            "youtube_video_count": self.youtube_video_count,
        }


@dataclass
class TrendScanResult:
    scan_id: str
    created_at: datetime
    sources: list[str]
    topics: list[RankedTopic]
