from __future__ import annotations

from src.trend_intelligence.types import RawTrendTopic, TopicScoreBreakdown, YouTubeVideoCandidate


def clamp_score(score: float) -> float:
    """Normalize any numeric score onto a 0-100 scale."""
    return round(max(0.0, min(100.0, score)), 2)


def scoreTrendMomentum(topic: RawTrendTopic) -> float:
    """Return a deterministic placeholder trend momentum score (0-100)."""
    # Placeholder logic: use weighted combination of available raw trend signals.
    # TODO: Replace with production trend model fed by historical topic trajectories.
    signal_component = topic.signal_strength * 60
    growth_component = topic.growth_rate * 25
    regional_component = topic.regional_interest * 15
    return clamp_score(signal_component + growth_component + regional_component)


def scoreWatchTimePotential(video_candidates: list[YouTubeVideoCandidate]) -> float:
    """Return a deterministic placeholder watch-time potential score (0-100)."""
    if not video_candidates:
        return 50.0

    # Placeholder logic: favor longer average duration and stronger engagement density.
    # TODO: Replace with a watch-time prediction model from channel/topic retention history.
    avg_duration_minutes = sum(v.duration_seconds for v in video_candidates) / len(video_candidates) / 60
    duration_component = min(60.0, avg_duration_minutes * 3.0)

    avg_engagement_rate = sum((v.likes + v.comments) / max(v.views, 1) for v in video_candidates) / len(video_candidates)
    engagement_component = min(40.0, avg_engagement_rate * 1200)

    return clamp_score(duration_component + engagement_component)


def scoreClickability(topic: str, video_candidates: list[YouTubeVideoCandidate]) -> float:
    """Return a deterministic placeholder clickability score (0-100)."""
    # Placeholder logic: combine known clickable keywords and view concentration.
    # TODO: Replace with a title/thumbnail CTR estimation pipeline.
    keyword_bonus_terms = ("why", "how", "secret", "collapse", "fall", "truth")
    keyword_bonus = 15.0 if any(term in topic.lower() for term in keyword_bonus_terms) else 0.0

    if not video_candidates:
        return clamp_score(45.0 + keyword_bonus)

    avg_views = sum(v.views for v in video_candidates) / len(video_candidates)
    max_views = max(v.views for v in video_candidates)
    view_ratio_component = 85.0 * (avg_views / max(max_views, 1))
    return clamp_score(view_ratio_component + keyword_bonus)


def scoreCompetitionGap(video_candidates: list[YouTubeVideoCandidate]) -> float:
    """Return a deterministic placeholder competition-gap score (0-100)."""
    if not video_candidates:
        return 85.0

    # Placeholder logic: fewer high-view incumbents means larger competition gap.
    # TODO: Replace with SERP saturation and competitor-authority analysis.
    high_competition_count = sum(1 for video in video_candidates if video.views >= 500_000)
    high_competition_ratio = high_competition_count / len(video_candidates)
    return clamp_score((1.0 - high_competition_ratio) * 100.0)


def scoreBrandAlignment(topic: str, brand_focus: str) -> float:
    """Return a deterministic placeholder brand-alignment score (0-100)."""
    if brand_focus.lower() == "all":
        return 70.0

    # Placeholder logic: simple token overlap between topic and selected brand focus.
    # TODO: Replace with semantic similarity against brand voice and content strategy docs.
    topic_terms = set(topic.lower().replace(":", " ").split())
    focus_terms = set(brand_focus.lower().replace(":", " ").split())
    overlap = len(topic_terms & focus_terms)
    coverage = overlap / max(len(focus_terms), 1)
    return clamp_score(40.0 + coverage * 60.0)


def scoreTopicOverall(
    *,
    trend_momentum: float,
    watch_time_potential: float,
    clickability: float,
    competition_gap: float,
    brand_alignment: float,
) -> float:
    """Return weighted overall topic score on 0-100 scale."""
    # Weights are intentionally explicit to keep this function pure and test-friendly.
    weighted_score = (
        clamp_score(trend_momentum) * 0.25
        + clamp_score(watch_time_potential) * 0.25
        + clamp_score(clickability) * 0.20
        + clamp_score(competition_gap) * 0.15
        + clamp_score(brand_alignment) * 0.15
    )
    return clamp_score(weighted_score)


def build_score_breakdown(
    *,
    trend_momentum: float,
    watch_time_potential: float,
    clickability: float,
    competition_gap: float,
    brand_alignment: float,
) -> TopicScoreBreakdown:
    """Create a normalized topic score breakdown from component scores."""
    return TopicScoreBreakdown(
        trend_momentum=clamp_score(trend_momentum),
        watch_time_potential=clamp_score(watch_time_potential),
        clickability=clamp_score(clickability),
        competition_gap=clamp_score(competition_gap),
        brand_alignment=clamp_score(brand_alignment),
        overall=scoreTopicOverall(
            trend_momentum=trend_momentum,
            watch_time_potential=watch_time_potential,
            clickability=clickability,
            competition_gap=competition_gap,
            brand_alignment=brand_alignment,
        ),
    )
