from __future__ import annotations

from src.ui.trend_intelligence_types import TopicInsight, TopicResult, TopicScoreBreakdown

SAMPLE_TOPIC_RESULTS: list[TopicResult] = [
    TopicResult(
        topic_title="Why the Bronze Age Collapse Happened in 50 Years",
        total_score=91,
        score_breakdown=TopicScoreBreakdown(
            trend_momentum_score=93,
            watch_time_potential_score=88,
            clickability_score=90,
            competition_gap_score=84,
            brand_alignment_score=98,
        ),
        insight=TopicInsight(
            reasoning=(
                "High audience curiosity around civilizational collapse themes, with room for "
                "evidence-driven storytelling that contrasts mainstream explanations."
            ),
            content_angle_ideas=[
                "Timeline breakdown by region (Mycenae, Hittites, Levant)",
                "Debunking three common myths in under 8 minutes",
                "Signal map: climate, war, trade, and migration interactions",
            ],
            hook_ideas=[
                "An entire world system vanished in one lifetime.",
                "What if the first global collapse was self-inflicted?",
                "This 3-part chain reaction rewrote history.",
            ],
            thumbnail_ideas=[
                "Burning palace silhouette + '50-Year Collapse' text",
                "Ancient map with red fracture lines",
                "Before/After civilization skyline split",
            ],
        ),
    ),
    TopicResult(
        topic_title="The Forgotten General Who Nearly Changed Rome",
        total_score=87,
        score_breakdown=TopicScoreBreakdown(
            trend_momentum_score=82,
            watch_time_potential_score=89,
            clickability_score=86,
            competition_gap_score=92,
            brand_alignment_score=86,
        ),
        insight=TopicInsight(
            reasoning=(
                "Underserved character-led military history topic with strong episodic potential and "
                "a favorable competition gap in long-form explainers."
            ),
            content_angle_ideas=[
                "One battle, three decisions that almost flipped Rome",
                "Character study: ambition vs. empire stability",
                "What modern strategists can learn from this campaign",
            ],
            hook_ideas=[
                "Rome almost lost everything to one overlooked rival.",
                "History forgot him. Rome didn't.",
                "This general came one decision away from immortality.",
            ],
            thumbnail_ideas=[
                "Roman eagle cracked in half",
                "Portrait silhouette with 'Forgotten General'",
                "Battle map + red arrow to Rome",
            ],
        ),
    ),
]
