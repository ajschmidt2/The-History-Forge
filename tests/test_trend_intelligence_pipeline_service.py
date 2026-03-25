from src.trend_intelligence.pipeline_service import TrendIntelligencePipelineService


def test_full_scan_pipeline_returns_normalized_topic_data_with_mocks():
    service = TrendIntelligencePipelineService()

    result = service.run_full_scan_pipeline(topic_limit=3, videos_per_topic=2)

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
