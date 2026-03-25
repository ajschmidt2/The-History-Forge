from src.trend_intelligence.adapters.mock_adapters import GoogleTrendsSeedsAdapter, MockTrendsSourceAdapter, MockYouTubeSourceAdapter
from src.trend_intelligence.pipeline_service import TrendIntelligencePipelineService
from src.trend_intelligence.types import TrendScanFilters


def test_full_scan_pipeline_returns_normalized_topic_data_with_mocks():
    service = TrendIntelligencePipelineService(
        trends_adapter=MockTrendsSourceAdapter(),
        youtube_adapter=MockYouTubeSourceAdapter(),
    )

    result = service.run_full_scan_pipeline(topic_limit=3, videos_per_topic=2, timeframe="24h")

    assert result.sources == ("mock_trends", "mock_youtube", "mock_analysis")
    assert len(result.topics) == 3

    first = result.topics[0]
    assert first.seed.topic
    assert first.seed.source == "mock_trends"
    assert len(first.videos) == 2
    assert first.videos[0].source == "mock_youtube"
    assert first.analysis.source == "mock_analysis"
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
