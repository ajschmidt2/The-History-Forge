from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from src.trend_intelligence.models import TopicInsight, TopicResult, TopicScoreBreakdown, TrendScanFilters


@dataclass
class TrendScanUiState:
    is_loading: bool
    error_message: str | None
    has_run: bool


def render_page_intro() -> None:
    st.header("📈 Trend Intelligence")
    st.caption(
        "Discover high-upside history topics, score their opportunity, and push winners into your content pipeline."
    )


def render_filter_panel(defaults: TrendScanFilters) -> TrendScanFilters:
    with st.container(border=True):
        st.subheader("Filter Panel")
        col1, col2 = st.columns(2)

        with col1:
            timeframe = st.segmented_control(
                "Timeframe",
                options=["24h", "7d", "30d"],
                default=defaults.timeframe,
                selection_mode="single",
            )
            brand_focus = st.selectbox(
                "Brand focus",
                options=["ancient history", "war history", "forgotten figures", "mysteries", "all"],
                index=["ancient history", "war history", "forgotten figures", "mysteries", "all"].index(
                    defaults.brand_focus
                ),
            )

        with col2:
            content_type = st.segmented_control(
                "Content type",
                options=["long-form", "shorts", "both"],
                default=defaults.content_type,
                selection_mode="single",
            )
            minimum_score = st.slider(
                "Minimum score (placeholder)",
                min_value=0,
                max_value=100,
                value=defaults.minimum_score,
                help="Placeholder control. This will be wired to backend query filtering in a future iteration.",
            )

        return TrendScanFilters(
            timeframe=timeframe or defaults.timeframe,
            content_type=content_type or defaults.content_type,
            brand_focus=brand_focus,
            minimum_score=minimum_score,
        )


def render_results(results: list[TopicResult], ui_state: TrendScanUiState) -> None:
    st.subheader("Results")

    if ui_state.is_loading:
        st.info("Running scan… scoring candidate topics and generating insights.")
        st.progress(45)
        return

    if ui_state.error_message:
        st.error(f"Trend scan failed: {ui_state.error_message}")
        return

    if not ui_state.has_run:
        st.info("No scan has been run yet. Set filters and click **Run Scan**.")
        return

    if not results:
        st.warning("No topics matched these filters.")
        return

    for index, topic in enumerate(results, start=1):
        render_topic_card(topic=topic, rank=index)


def render_topic_card(topic: TopicResult, rank: int) -> None:
    with st.container(border=True):
        st.subheader(f"#{rank} {topic.topic_title}")
        st.metric("Total Score", f"{topic.score.total_score:.1f}")

        a, b, c, d, e = st.columns(5)
        a.metric("Trend Momentum", f"{topic.score.trend_momentum_score:.1f}")
        b.metric("Watch-Time Potential", f"{topic.score.watch_time_potential_score:.1f}")
        c.metric("Clickability", f"{topic.score.clickability_score:.1f}")
        d.metric("Competition Gap", f"{topic.score.competition_gap_score:.1f}")
        e.metric("Brand Alignment", f"{topic.score.brand_alignment_score:.1f}")

        st.caption(topic.insight.reasoning)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("**Content angle ideas**")
            for idea in topic.insight.content_angle_ideas:
                st.markdown(f"- {idea}")
        with col2:
            st.markdown("**Hook ideas**")
            for idea in topic.insight.hook_ideas:
                st.markdown(f"- {idea}")
        with col3:
            st.markdown("**Thumbnail ideas**")
            for idea in topic.insight.thumbnail_ideas:
                st.markdown(f"- {idea}")

        if st.button("Save to Pipeline", key=f"save_trend_{rank}_{topic.topic_title}", use_container_width=True):
            st.session_state.topic = topic.topic_title
            st.session_state.project_title = topic.topic_title
            st.success(f"Saved '{topic.topic_title}' to pipeline.")


def mocked_topic_results() -> list[TopicResult]:
    return [
        TopicResult(
            topic_title="How the Bronze Age Collapse Reshaped the Ancient World",
            score=TopicScoreBreakdown(
                total_score=86.4,
                trend_momentum_score=88.0,
                watch_time_potential_score=84.5,
                clickability_score=82.0,
                competition_gap_score=79.5,
                brand_alignment_score=94.0,
            ),
            insight=TopicInsight(
                reasoning="Search and video interest are rising around systemic collapse stories, with a strong fit for explanatory history formats.",
                content_angle_ideas=[
                    "Five domino events that triggered the collapse",
                    "What modern systems can learn from 1200 BCE",
                    "The mystery of the Sea Peoples, evidence vs myth",
                ],
                hook_ideas=[
                    "A global civilization failed in under 50 years—here's why",
                    "The first world crisis wasn't modern",
                    "One historical mystery still has no final answer",
                ],
                thumbnail_ideas=[
                    "Burning map overlay with '1200 BCE'",
                    "Collapsed statue + red timeline arrow",
                    "Split panel: thriving empire vs ruins",
                ],
            ),
        ),
        TopicResult(
            topic_title="The Forgotten General Who Nearly Changed WWII",
            score=TopicScoreBreakdown(
                total_score=81.8,
                trend_momentum_score=74.0,
                watch_time_potential_score=86.0,
                clickability_score=88.0,
                competition_gap_score=83.0,
                brand_alignment_score=78.0,
            ),
            insight=TopicInsight(
                reasoning="Underserved figure-led war-history stories show strong click-through patterns and above-average retention in competing channels.",
                content_angle_ideas=[
                    "Profile of an overlooked strategist",
                    "Three decisions that altered campaign outcomes",
                    "How history books minimized their role",
                ],
                hook_ideas=[
                    "WWII had a hidden mastermind nobody talks about",
                    "The general history forgot",
                    "One commander, one gamble, massive consequences",
                ],
                thumbnail_ideas=[
                    "Portrait cutout + stamped text 'FORGOTTEN'",
                    "Battle map with highlighted maneuver",
                    "Archival black-and-white image + question mark",
                ],
            ),
        ),
    ]
