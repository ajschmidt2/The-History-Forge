from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TrendingTopicSeed:
    topic: str
    source: str
    momentum: float
    reason: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VideoResult:
    topic: str
    video_id: str
    title: str
    channel_title: str
    view_count: int
    like_count: int
    comment_count: int
    published_at: str
    duration_minutes: float
    source: str


@dataclass(frozen=True)
class TopicAnalysis:
    topic: str
    explanation: str
    angles: tuple[str, ...]
    hooks: tuple[str, ...]
    thumbnail_ideas: tuple[str, ...]
    source: str
