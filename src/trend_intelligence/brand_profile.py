from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BrandPreference:
    """Editable preference block used by brand-alignment scoring."""

    key: str
    weight: float
    keywords: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class ChannelPerformanceSnapshot:
    """
    Optional prior-channel metadata for future scoring upgrades.

    This structure is intentionally lightweight so future integrations can pass
    persisted script/video outcomes (CTR, retention, watch-time, etc.) without
    changing the scoring call signatures again.
    """

    topic_tags: tuple[str, ...] = ()
    avg_ctr: float | None = None
    avg_retention: float | None = None
    avg_watch_time_minutes: float | None = None


@dataclass(frozen=True)
class BrandProfile:
    """Single source of truth for brand-fit scoring knobs."""

    profile_id: str
    display_name: str
    baseline_alignment_score: float
    preference_match_scale: float
    focus_boost_scale: float
    long_form_keyword_bonus: float
    channel_performance_weight: float
    preferences: tuple[BrandPreference, ...]
    overall_score_weights: dict[str, float] = field(default_factory=dict)


# Central config location for Trend Intelligence ranking behavior.
HISTORY_CROSSROADS_BRAND_PROFILE = BrandProfile(
    profile_id="history_crossroads",
    display_name="History Crossroads",
    baseline_alignment_score=32.0,
    preference_match_scale=56.0,
    focus_boost_scale=12.0,
    long_form_keyword_bonus=8.0,
    channel_performance_weight=0.10,
    preferences=(
        BrandPreference(
            key="ancient_mysteries",
            weight=0.24,
            keywords=(
                "ancient", "pharaoh", "lost city", "ruins", "mystery", "artifact",
                "egypt", "rome", "greek", "babylon", "mesopotamia", "bronze age",
                "iron age", "civilization", "empire", "dynasty", "pyramid", "tomb",
            ),
            description="Ancient mysteries and unresolved historical puzzles.",
        ),
        BrandPreference(
            key="wartime_hero_stories",
            weight=0.22,
            keywords=(
                "war", "battle", "resistance", "hero", "medal", "veteran",
                "wwii", "world war", "civil war", "revolution", "siege", "invasion",
                "military", "soldier", "general", "fleet", "campaign", "conflict",
            ),
            description="Heroic wartime events and individuals under pressure.",
        ),
        BrandPreference(
            key="forgotten_individuals",
            weight=0.20,
            keywords=(
                "forgotten", "unknown", "unsung", "trailblazer", "figure", "biography",
                "untold story", "pioneer", "overlooked", "real story", "true story of",
                "life of", "who was", "person behind",
            ),
            description="Underrated historical figures and overlooked biographies.",
        ),
        BrandPreference(
            key="bizarre_true_stories",
            weight=0.18,
            keywords=(
                "bizarre", "unbelievable", "strange", "impossible", "hoax",
                "scandal", "conspiracy", "secret", "hidden", "suppressed",
                "dark history", "untold", "shocking", "what really happened",
            ),
            description="Real events that feel unbelievable yet verifiable.",
        ),
        BrandPreference(
            key="long_form_documentary",
            weight=0.16,
            keywords=(
                "documentary", "archive", "timeline", "investigation", "deep dive", "chronicle",
                "history of", "rise and fall", "collapse of", "origins of", "explained",
                "complete history", "full story",
            ),
            description="Topics that can sustain long-form documentary storytelling.",
        ),
    ),
    overall_score_weights={
        "trend_momentum": 0.18,
        "watch_time_potential": 0.22,
        "clickability": 0.16,
        "competition_gap": 0.14,
        "brand_alignment": 0.30,
    },
)


DEFAULT_BRAND_PROFILE = HISTORY_CROSSROADS_BRAND_PROFILE
