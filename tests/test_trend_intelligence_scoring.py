from datetime import datetime, timezone

from src.trend_intelligence.scoring import (
    build_score_breakdown,
    scoreBrandAlignment,
    scoreClickability,
    scoreCompetitionGap,
    scoreTopicOverall,
    scoreTrendMomentum,
    scoreWatchTimePotential,
)
from src.trend_intelligence.types import RawTrendTopic, YouTubeVideoCandidate


def _video(*, views: int, likes: int, comments: int, duration_seconds: int) -> YouTubeVideoCandidate:
    return YouTubeVideoCandidate(
        video_id=f"v-{views}",
        title="Sample",
        channel_title="History Channel",
        views=views,
        likes=likes,
        comments=comments,
        duration_seconds=duration_seconds,
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_individual_scores_are_deterministic_and_normalized():
    topic = RawTrendTopic(
        topic="Bronze Age Collapse",
        source="test",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        signal_strength=0.9,
        growth_rate=0.4,
        regional_interest=0.6,
    )
    videos = [
        _video(views=100_000, likes=2_000, comments=400, duration_seconds=1_200),
        _video(views=300_000, likes=7_500, comments=1_000, duration_seconds=2_100),
    ]

    assert 0 <= scoreTrendMomentum(topic) <= 100
    assert 0 <= scoreWatchTimePotential(videos) <= 100
    assert 0 <= scoreClickability(topic.topic, videos) <= 100
    assert 0 <= scoreCompetitionGap(videos) <= 100
    assert 0 <= scoreBrandAlignment(topic.topic, "ancient history") <= 100


def test_overall_score_uses_expected_weights():
    overall = scoreTopicOverall(
        trend_momentum=80,
        watch_time_potential=60,
        clickability=70,
        competition_gap=50,
        brand_alignment=40,
    )

    expected = (80 * 0.25) + (60 * 0.25) + (70 * 0.20) + (50 * 0.15) + (40 * 0.15)
    assert overall == expected


def test_build_score_breakdown_clamps_and_sets_overall():
    breakdown = build_score_breakdown(
        trend_momentum=130,
        watch_time_potential=-10,
        clickability=50,
        competition_gap=60,
        brand_alignment=70,
    )

    assert breakdown.trend_momentum == 100
    assert breakdown.watch_time_potential == 0
    assert 0 <= breakdown.overall <= 100
