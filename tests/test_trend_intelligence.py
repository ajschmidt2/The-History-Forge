from src.trend_intelligence.analyzer import rank_topics
from src.trend_intelligence.models import TrendSignal, VideoSignal
from src.trend_intelligence.service import TrendIntelligenceService


class _TrendAdapter:
    source_name = "test-trend"

    def fetch_topics(self, *, limit: int):
        return [
            TrendSignal(topic="The Fall of Rome", source=self.source_name, momentum=0.9, reason="Spike"),
            TrendSignal(topic="The Fall of Rome", source=self.source_name, momentum=0.8, reason="Duplicate"),
        ][:limit]


class _VideoAdapter:
    source_name = "test-video"

    def search_videos(self, topic: str, *, limit: int):
        return [
            VideoSignal(
                topic=topic,
                title="Why Rome Fell",
                channel_title="History Channel",
                view_count=1_000_000,
                like_count=10000,
                comment_count=500,
                published_at="2026-01-01T00:00:00Z",
                duration_minutes=35.0,
            )
        ][:limit]


class _Repo:
    def __init__(self):
        self.status = []

    def create_scan(self, *, project_id: str, source_names: list[str], status: str) -> str:
        return "scan-1"

    def save_topics(self, scan_id: str, ranked_topics):
        self.saved = list(ranked_topics)

    def update_scan_status(self, scan_id: str, status: str, error_message: str | None = None):
        self.status.append(status)


def test_rank_topics_returns_subscores():
    topics = [TrendSignal(topic="Bronze Age collapse", source="t", momentum=0.8, reason="Rising")]
    videos = {
        "Bronze Age collapse": [
            VideoSignal(
                topic="Bronze Age collapse",
                title="The real reason civilizations collapsed",
                channel_title="History Deep Dive",
                view_count=200_000,
                like_count=5000,
                comment_count=250,
                published_at="2026-01-01T00:00:00Z",
                duration_minutes=22.0,
            )
        ]
    }
    ranked = rank_topics(topics, videos)
    assert len(ranked) == 1
    assert ranked[0].score.total > 0
    assert ranked[0].score.watch_time > 0


def test_service_dedupes_topics_and_persists():
    repo = _Repo()
    svc = TrendIntelligenceService(
        trend_adapters=[_TrendAdapter()],
        video_adapters=[_VideoAdapter()],
        repository=repo,
    )
    result = svc.scan(project_id="proj-1", topic_limit=10, videos_per_topic=5)
    assert len(result.topics) == 1
    assert repo.status[-1] == "completed"
