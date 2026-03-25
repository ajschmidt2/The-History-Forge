from __future__ import annotations

from abc import ABC, abstractmethod

from src.trend_intelligence.models import TrendSignal, VideoSignal


class TrendSourceAdapter(ABC):
    source_name: str

    @abstractmethod
    def fetch_topics(self, *, limit: int) -> list[TrendSignal]:
        raise NotImplementedError


class VideoSourceAdapter(ABC):
    source_name: str

    @abstractmethod
    def search_videos(self, topic: str, *, limit: int) -> list[VideoSignal]:
        raise NotImplementedError
