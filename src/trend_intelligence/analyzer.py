from __future__ import annotations

from collections import Counter

from src.trend_intelligence.models import RankedTopic, TopicScore, TrendSignal, VideoSignal


HISTORY_BRAND_TERMS = {
    "history",
    "war",
    "empire",
    "battle",
    "collapse",
    "ancient",
    "revolution",
    "dynasty",
    "civilization",
    "crossroads",
}


def rank_topics(signals: list[TrendSignal], videos_by_topic: dict[str, list[VideoSignal]]) -> list[RankedTopic]:
    ranked: list[RankedTopic] = []
    for signal in signals:
        videos = videos_by_topic.get(signal.topic, [])
        score = TopicScore(
            momentum=round(_bounded(signal.momentum), 2),
            watch_time=round(_watch_time_score(videos), 2),
            clickability=round(_clickability_score(videos, signal.topic), 2),
            competition_gap=round(_competition_gap(videos), 2),
            brand_alignment=round(_brand_alignment(signal.topic, videos), 2),
        )
        ranked.append(
            RankedTopic(
                title=signal.topic,
                score=score,
                why_trending=signal.reason,
                content_angles=_angles(signal.topic),
                hooks=_hooks(signal.topic),
                thumbnail_ideas=_thumbnail_ideas(signal.topic),
                trend_source=signal.source,
                youtube_video_count=len(videos),
            )
        )

    return sorted(ranked, key=lambda topic: topic.score.total, reverse=True)


def _watch_time_score(videos: list[VideoSignal]) -> float:
    if not videos:
        return 0.52
    long_ratio = sum(1 for v in videos if v.duration_minutes >= 12) / len(videos)
    return _bounded(0.45 + (long_ratio * 0.55))


def _clickability_score(videos: list[VideoSignal], topic: str) -> float:
    if not videos:
        return 0.5
    avg_views = sum(v.view_count for v in videos) / max(1, len(videos))
    max_views = max(v.view_count for v in videos)
    keyword_bonus = 0.1 if any(word in topic.lower() for word in ["why", "fall", "secret", "collapse"]) else 0.0
    normalized = min(1.0, (avg_views / max(max_views, 1)) + keyword_bonus)
    return _bounded(0.35 + normalized * 0.65)


def _competition_gap(videos: list[VideoSignal]) -> float:
    if not videos:
        return 0.8
    highly_competitive = sum(1 for v in videos if v.view_count > 500_000)
    share = highly_competitive / len(videos)
    return _bounded(1.0 - share)


def _brand_alignment(topic: str, videos: list[VideoSignal]) -> float:
    topic_words = set(topic.lower().replace(":", " ").split())
    keyword_hits = len(topic_words & HISTORY_BRAND_TERMS)

    channel_terms = Counter(
        " ".join(v.channel_title.lower() for v in videos).split()
    )
    channel_bonus = 0.1 if any(term in channel_terms for term in {"history", "documentary", "archives"}) else 0.0
    return _bounded(0.4 + (keyword_hits * 0.12) + channel_bonus)


def _angles(topic: str) -> list[str]:
    return [
        f"What most people miss about {topic}",
        f"The turning point that changed {topic}",
        f"How {topic} still shapes geopolitics today",
    ]


def _hooks(topic: str) -> list[str]:
    return [
        f"This one decision made {topic} inevitable.",
        f"The 10-minute story of {topic} nobody teaches in school.",
        f"If you only watch one video on {topic}, make it this one.",
    ]


def _thumbnail_ideas(topic: str) -> list[str]:
    return [
        f"Split-image: peak vs collapse of {topic}",
        f"Map + bold red arrow + '{topic}: WHY NOW?'",
        f"Historic portrait with shocked reaction text: 'THE TURNING POINT'",
    ]


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, value))
