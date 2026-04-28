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


def test_watch_time_scoring_responds_to_content_type():
    shorts = [
        _video(views=90_000, likes=4_500, comments=320, duration_seconds=55),
        _video(views=140_000, likes=6_800, comments=510, duration_seconds=62),
    ]
    long_form = [
        _video(views=220_000, likes=8_400, comments=900, duration_seconds=900),
        _video(views=310_000, likes=11_000, comments=1300, duration_seconds=1320),
    ]

    assert scoreWatchTimePotential(shorts, content_type="shorts") > scoreWatchTimePotential(shorts, content_type="long-form")
    assert scoreWatchTimePotential(long_form, content_type="long-form") > scoreWatchTimePotential(long_form, content_type="shorts")


def test_clickability_rewards_curiosity_packaging_terms():
    videos = [
        YouTubeVideoCandidate(
            video_id="v1",
            title="The Forgotten General Who Changed WWII",
            channel_title="History Channel",
            views=120_000,
            likes=4_000,
            comments=350,
            duration_seconds=700,
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        YouTubeVideoCandidate(
            video_id="v2",
            title="The Secret Behind Rome's Sudden Collapse",
            channel_title="History Channel",
            views=240_000,
            likes=8_000,
            comments=700,
            duration_seconds=900,
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    ]

    strong = scoreClickability("The Forgotten General Who Changed WWII", videos)
    plain = scoreClickability("World War II military strategy", videos)

    assert strong > plain


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
