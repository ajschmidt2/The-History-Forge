from src.trend_intelligence.adapters.mock_adapters import GoogleTrendsSeedsAdapter, MockTrendsSourceAdapter, MockYouTubeSourceAdapter
import src.trend_intelligence.pipeline_service as pipeline_mod
from src.trend_intelligence.pipeline_service import TrendIntelligencePipelineService
from src.trend_intelligence.types import TrendScanFilters


def test_full_scan_pipeline_returns_normalized_topic_data_with_mocks():
    service = TrendIntelligencePipelineService(
        trends_adapter=MockTrendsSourceAdapter(),
        youtube_adapter=MockYouTubeSourceAdapter(),
    )

    result = service.run_full_scan_pipeline(topic_limit=3, videos_per_topic=2, timeframe="24h")

    assert result.sources[:2] == ("mock_trends", "mock_youtube")
    assert result.sources[2]
    assert len(result.topics) == 3

    first = result.topics[0]
    assert first.seed.topic
    assert first.seed.source == "mock_trends"
    assert len(first.videos) == 2
    assert first.videos[0].source == "mock_youtube"
    assert first.analysis.source in {"openai_topic_analysis", "deterministic_topic_analysis", "mock_analysis"}
    assert first.analysis.angles
    assert first.analysis.hooks
    assert first.analysis.thumbnail_ideas
    assert first.seed.raw["timeframe"] == "24h"


def test_google_trends_adapter_falls_back_to_mock_on_errors():
    adapter = GoogleTrendsSeedsAdapter(fallback=MockTrendsSourceAdapter())
    adapter._fetch_from_google_rss = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[attr-defined]

    topics = adapter.fetch_trending_topics(limit=2, timeframe="7d")

    assert len(topics) == 2
    assert topics[0].source == "mock_trends"


def test_run_trend_intelligence_scan_accepts_filters_and_returns_sorted_topic_results():
    service = TrendIntelligencePipelineService(
        trends_adapter=MockTrendsSourceAdapter(),
        youtube_adapter=MockYouTubeSourceAdapter(),
    )
    filters = TrendScanFilters(
        timeframe="7d",
        content_type="both",
        brand_focus="ancient history",
        minimum_score=0,
    )

    results = service.run_trend_intelligence_scan(filters, topic_limit=4, videos_per_topic=3, max_workers=2)

    assert len(results) == 4
    assert all(result.score.overall >= 0 for result in results)
    assert results == sorted(results, key=lambda result: (-result.score.overall, result.topic.lower()))

    first = results[0]
    assert "trend=" in first.source
    assert "analysis=" in first.source
    assert len(first.sampled_videos) == 3
    assert first.insight.why_now
    assert first.insight.opportunities
    assert first.insight.risks


def test_run_trend_intelligence_scan_applies_minimum_score_filter():
    service = TrendIntelligencePipelineService(
        trends_adapter=MockTrendsSourceAdapter(),
        youtube_adapter=MockYouTubeSourceAdapter(),
    )
    filters = TrendScanFilters(
        timeframe="7d",
        content_type="both",
        brand_focus="all",
        minimum_score=95,
    )

    results = service.run_trend_intelligence_scan(filters, topic_limit=5, videos_per_topic=2, max_workers=3)

    assert all(result.score.overall >= 95 for result in results)


def test_pipeline_service_survives_missing_youtube_key(monkeypatch):
    class _DisabledYoutube:
        source_name = "youtube_data_api"
        enabled = False

        def search_topic_videos(self, _topic: str, *, limit: int):
            return [] if limit > 0 else []

    monkeypatch.setattr(pipeline_mod, "YouTubeTopicSourceAdapter", _DisabledYoutube)
    service = TrendIntelligencePipelineService(trends_adapter=MockTrendsSourceAdapter())

    filters = TrendScanFilters(timeframe="7d", content_type="both", brand_focus="all", minimum_score=0)
    execution = service.run_trend_intelligence_scan_with_status(filters, topic_limit=2, videos_per_topic=2, max_workers=1)

    assert service.youtube_adapter is not None
    assert len(execution.topics) == 2


def test_pipeline_service_records_youtube_init_error(monkeypatch):
    class _BrokenYoutube:
        def __init__(self):
            raise RuntimeError("bad youtube config")

    monkeypatch.setattr(pipeline_mod, "YouTubeTopicSourceAdapter", _BrokenYoutube)
    service = TrendIntelligencePipelineService(trends_adapter=GoogleTrendsSeedsAdapter())

    assert service.youtube_adapter is None
    assert service.youtube_adapter_error is not None


def test_pipeline_service_passes_content_type_to_youtube_and_analysis():
    class _SpyYoutube:
        source_name = "spy_youtube"

        def __init__(self):
            self.calls = []

        def search_topic_videos(self, topic: str, *, limit: int, content_type: str = "both"):
            self.calls.append((topic, limit, content_type))
            return MockYouTubeSourceAdapter().search_topic_videos(topic, limit=limit, content_type=content_type)

    class _SpyAnalysis:
        source_name = "spy_analysis"

        def __init__(self):
            self.calls = []

        def analyze_topic(self, topic: str, videos, *, brand_focus: str = "all", content_type: str = "both"):
            self.calls.append((topic, brand_focus, content_type, len(videos)))
            from src.trend_intelligence.adapters.topic_analysis_adapter import DeterministicTopicAnalysisAdapter

            return DeterministicTopicAnalysisAdapter().analyze_topic(
                topic,
                videos,
                brand_focus=brand_focus,
                content_type=content_type,
            )

    youtube = _SpyYoutube()
    analysis = _SpyAnalysis()
    service = TrendIntelligencePipelineService(
        trends_adapter=MockTrendsSourceAdapter(),
        youtube_adapter=youtube,
        analysis_adapter=analysis,
    )
    filters = TrendScanFilters(
        timeframe="7d",
        content_type="shorts",
        brand_focus="mysteries",
        minimum_score=0,
    )

    service.run_trend_intelligence_scan(filters, topic_limit=2, videos_per_topic=2, max_workers=1)

    assert youtube.calls
    assert all(call[2] == "shorts" for call in youtube.calls)
    assert analysis.calls
    assert all(call[2] == "shorts" for call in analysis.calls)
