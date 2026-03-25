from src.trend_intelligence.adapters.mock_adapters import MockYouTubeSourceAdapter
from src.trend_intelligence.adapters.topic_analysis_adapter import (
    DeterministicTopicAnalysisAdapter,
    OpenAITopicAnalysisAdapter,
    _parse_response,
)


def test_deterministic_adapter_returns_required_shape():
    topic = "The Fall of Constantinople"
    videos = MockYouTubeSourceAdapter().search_topic_videos(topic, limit=3)

    analysis = DeterministicTopicAnalysisAdapter().analyze_topic(topic, videos)

    assert analysis.topic == topic
    assert analysis.source == "deterministic_topic_analysis"
    assert analysis.explanation
    assert len(analysis.angles) == 3
    assert len(analysis.hooks) == 3
    assert len(analysis.thumbnail_ideas) == 3


def test_openai_adapter_falls_back_without_api_key():
    topic = "The Suez Crisis"
    videos = MockYouTubeSourceAdapter().search_topic_videos(topic, limit=2)

    adapter = OpenAITopicAnalysisAdapter()
    adapter._api_key = ""
    analysis = adapter.analyze_topic(topic, videos)

    assert analysis.source == "deterministic_topic_analysis"
    assert len(analysis.angles) == 3
    assert len(analysis.hooks) == 3
    assert len(analysis.thumbnail_ideas) == 3


def test_parse_response_requires_exact_structured_fields():
    payload = """
    {
      "why_trending": "A surge in related explainers indicates revived audience curiosity.",
      "content_angles": ["Angle 1", "Angle 2", "Angle 3"],
      "opening_hooks": ["Hook 1", "Hook 2", "Hook 3"],
      "thumbnail_ideas": ["Thumb 1", "Thumb 2", "Thumb 3"]
    }
    """

    parsed = _parse_response(payload)

    assert parsed["why_trending"]
    assert len(parsed["content_angles"]) == 3
    assert len(parsed["opening_hooks"]) == 3
    assert len(parsed["thumbnail_ideas"]) == 3
