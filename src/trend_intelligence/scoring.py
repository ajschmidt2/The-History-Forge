from __future__ import annotations

from src.trend_intelligence.brand_profile import (
    DEFAULT_BRAND_PROFILE,
    BrandProfile,
    ChannelPerformanceSnapshot,
)
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


def scoreBrandAlignment(
    topic: str,
    brand_focus: str,
    *,
    profile: BrandProfile = DEFAULT_BRAND_PROFILE,
    channel_performance: ChannelPerformanceSnapshot | None = None,
) -> float:
    """
    Return brand-alignment score (0-100) using the configured brand profile.

    Future integration: when prior script/video performance is persisted, pass a
    ChannelPerformanceSnapshot to add topic-conditioned performance lift without
    forcing runtime coupling to storage in this version.
    """
    topic_text = topic.lower()
    focus_text = brand_focus.lower()

    preference_signal = 0.0
    for preference in profile.preferences:
        if any(keyword in topic_text for keyword in preference.keywords):
            preference_signal += preference.weight

    # Optional user-selected focus still contributes, but profile preferences are primary.
    focus_bonus = 0.0
    if focus_text != "all":
        focus_tokens = tuple(token for token in focus_text.replace(":", " ").split() if token)
        if focus_tokens:
            focus_hits = sum(1 for token in focus_tokens if token in topic_text)
            focus_bonus = (focus_hits / len(focus_tokens)) * profile.focus_boost_scale

    long_form_bonus = 0.0
    if any(marker in topic_text for marker in ("untold", "story", "archive", "timeline", "investigation")):
        long_form_bonus = profile.long_form_keyword_bonus

    performance_lift = _score_channel_performance_lift(
        topic=topic_text,
        snapshot=channel_performance,
        profile=profile,
    )

    return clamp_score(
        profile.baseline_alignment_score
        + (preference_signal * profile.preference_match_scale)
        + focus_bonus
        + long_form_bonus
        + performance_lift
    )


def _score_channel_performance_lift(
    *,
    topic: str,
    snapshot: ChannelPerformanceSnapshot | None,
    profile: BrandProfile,
) -> float:
    """
    Optional bridge for future channel-performance-aware scoring.

    Current behavior is intentionally conservative so no persistence layer is
    required: if no snapshot metadata is provided, score contribution is zero.
    """
    if snapshot is None:
        return 0.0

    tag_overlap = sum(1 for tag in snapshot.topic_tags if tag and tag.lower() in topic)
    topic_tag_component = min(1.0, tag_overlap / 3.0)

    # Neutral defaults preserve backwards-compatible ranking when signals are missing.
    ctr_component = (snapshot.avg_ctr or 0.05) / 0.10
    retention_component = (snapshot.avg_retention or 0.40) / 0.60
    watch_time_component = (snapshot.avg_watch_time_minutes or 4.0) / 10.0

    normalized_signal = max(0.0, min(1.0, (topic_tag_component + ctr_component + retention_component + watch_time_component) / 4.0))
    return normalized_signal * (profile.channel_performance_weight * 100.0)


def scoreTopicOverall(
    *,
    trend_momentum: float,
    watch_time_potential: float,
    clickability: float,
    competition_gap: float,
    brand_alignment: float,
    profile: BrandProfile = DEFAULT_BRAND_PROFILE,
) -> float:
    """Return weighted overall topic score on 0-100 scale."""
    # Weights are centralized in brand_profile.py to keep scoring easy to tune.
    weights = profile.overall_score_weights
    weighted_score = (
        clamp_score(trend_momentum) * weights["trend_momentum"]
        + clamp_score(watch_time_potential) * weights["watch_time_potential"]
        + clamp_score(clickability) * weights["clickability"]
        + clamp_score(competition_gap) * weights["competition_gap"]
        + clamp_score(brand_alignment) * weights["brand_alignment"]
    )
    return clamp_score(weighted_score)


def build_score_breakdown(
    *,
    trend_momentum: float,
    watch_time_potential: float,
    clickability: float,
    competition_gap: float,
    brand_alignment: float,
    profile: BrandProfile = DEFAULT_BRAND_PROFILE,
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
            profile=profile,
        ),
    )
