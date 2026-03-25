from __future__ import annotations

from datetime import datetime, timezone

from src.trend_intelligence.adapters.base import TrendSourceAdapter, VideoSourceAdapter
from src.trend_intelligence.adapters.google_trends_rss import GoogleTrendsRssAdapter
from src.trend_intelligence.adapters.placeholders import MockTrendAdapter, PlaceholderVideoAdapter
from src.trend_intelligence.adapters.youtube_data_api import YouTubeDataApiAdapter
from src.trend_intelligence.analyzer import rank_topics
from src.trend_intelligence.models import TrendScanResult
from src.trend_intelligence.repository import TrendIntelligenceRepository


class TrendIntelligenceService:
    def __init__(
        self,
        *,
        trend_adapters: list[TrendSourceAdapter] | None = None,
        video_adapters: list[VideoSourceAdapter] | None = None,
        repository: TrendIntelligenceRepository | None = None,
    ) -> None:
        self.trend_adapters = trend_adapters or [GoogleTrendsRssAdapter(), MockTrendAdapter()]
        self.video_adapters = video_adapters or [YouTubeDataApiAdapter(), PlaceholderVideoAdapter()]
        self.repository = repository or TrendIntelligenceRepository()

    def scan(self, *, project_id: str, topic_limit: int = 8, videos_per_topic: int = 10) -> TrendScanResult:
        source_names = [adapter.source_name for adapter in self.trend_adapters + self.video_adapters]
        scan_id = self.repository.create_scan(project_id=project_id, source_names=source_names, status="running")

        try:
            trend_signals = self._load_trends(topic_limit)
            videos_by_topic: dict[str, list] = {}
            for signal in trend_signals:
                topic_videos = []
                for adapter in self.video_adapters:
                    try:
                        topic_videos.extend(adapter.search_videos(signal.topic, limit=videos_per_topic))
                    except Exception:
                        continue
                videos_by_topic[signal.topic] = topic_videos

            ranked = rank_topics(trend_signals, videos_by_topic)
            self.repository.save_topics(scan_id, ranked)
            self.repository.update_scan_status(scan_id, "completed")
            return TrendScanResult(
                scan_id=scan_id,
                created_at=datetime.now(timezone.utc),
                sources=source_names,
                topics=ranked,
            )
        except Exception as exc:
            self.repository.update_scan_status(scan_id, "failed", str(exc))
            raise

    def _load_trends(self, limit: int):
        merged = []
        seen = set()
        for adapter in self.trend_adapters:
            try:
                items = adapter.fetch_topics(limit=limit)
            except Exception:
                continue
            for item in items:
                normalized = item.topic.lower().strip()
                if normalized in seen:
                    continue
                seen.add(normalized)
                merged.append(item)
                if len(merged) >= limit:
                    return merged
        return merged
