from __future__ import annotations

import streamlit as st

from src.ui.trend_intelligence_types import ScriptBuilderPayload, TopicResult, TrendScanFilters


def render_page_header() -> None:
    st.header("📈 Trend Intelligence")
    st.caption(
        "Scan emerging history content opportunities with score-based signals and practical creative angles."
    )


def render_filter_panel(default_filters: TrendScanFilters) -> TrendScanFilters:
    st.subheader("Filters")
    timeframe = st.selectbox(
        "Timeframe",
        options=["24h", "7d", "30d"],
        index=["24h", "7d", "30d"].index(default_filters.timeframe),
        key="trend_scan_timeframe",
    )

    content_type = st.selectbox(
        "Content Type",
        options=["long-form", "shorts", "both"],
        index=["long-form", "shorts", "both"].index(default_filters.content_type),
        key="trend_scan_content_type",
    )

    brand_focus = st.selectbox(
        "Brand Focus",
        options=["ancient history", "war history", "forgotten figures", "mysteries", "all"],
        index=["ancient history", "war history", "forgotten figures", "mysteries", "all"].index(
            default_filters.brand_focus
        ),
        key="trend_scan_brand_focus",
    )

    min_score = st.slider(
        "Minimum Score",
        min_value=0,
        max_value=100,
        value=default_filters.min_score,
        step=5,
        key="trend_scan_min_score",
    )

    return TrendScanFilters(
        timeframe=timeframe,
        content_type=content_type,
        brand_focus=brand_focus,
        min_score=min_score,
    )


def _options_or_fallback(options: list[str], fallback: str) -> list[str]:
    sanitized = [str(item).strip() for item in options if str(item).strip()]
    return sanitized if sanitized else [fallback]


def render_topic_card(topic: TopicResult, idx: int) -> ScriptBuilderPayload | None:
    with st.container(border=True):
        st.subheader(topic.topic_title)

        score_cols = st.columns(3)
        score_cols[0].metric("Total Score", topic.total_score)
        score_cols[1].metric("Trend Momentum", topic.score_breakdown.trend_momentum_score)
        score_cols[2].metric("Watch-Time Potential", topic.score_breakdown.watch_time_potential_score)

        score_cols_2 = st.columns(3)
        score_cols_2[0].metric("Clickability", topic.score_breakdown.clickability_score)
        score_cols_2[1].metric("Competition Gap", topic.score_breakdown.competition_gap_score)
        score_cols_2[2].metric("Brand Alignment", topic.score_breakdown.brand_alignment_score)

        st.markdown(f"**Reasoning:** {topic.insight.reasoning}")

        idea_cols = st.columns(3)
        with idea_cols[0]:
            st.markdown("**Content Angle Ideas**")
            for angle in topic.insight.content_angle_ideas:
                st.markdown(f"- {angle}")

        with idea_cols[1]:
            st.markdown("**Hook Ideas**")
            for hook in topic.insight.hook_ideas:
                st.markdown(f"- {hook}")

        with idea_cols[2]:
            st.markdown("**Thumbnail Ideas**")
            for thumb in topic.insight.thumbnail_ideas:
                st.markdown(f"- {thumb}")

        selected_angle = st.selectbox(
            "Preferred content angle",
            options=_options_or_fallback(topic.insight.content_angle_ideas, "Balanced historical breakdown"),
            key=f"trend_preferred_angle_{idx}",
        )
        selected_hook = st.selectbox(
            "Selected hook",
            options=_options_or_fallback(topic.insight.hook_ideas, "Why this story is exploding right now"),
            key=f"trend_selected_hook_{idx}",
        )
        selected_thumbnail_direction = st.selectbox(
            "Thumbnail direction",
            options=_options_or_fallback(topic.insight.thumbnail_ideas, "High-contrast key figure + conflict text"),
            key=f"trend_thumbnail_direction_{idx}",
        )

        if st.button("Send to Script Builder", key=f"trend_save_pipeline_{idx}", use_container_width=True):
            return ScriptBuilderPayload(
                topic_title=topic.topic_title,
                why_may_be_trending=topic.insight.reasoning,
                preferred_content_angle=selected_angle,
                selected_hook=selected_hook,
                thumbnail_direction=selected_thumbnail_direction,
                score_breakdown=topic.score_breakdown,
            )
        return None


def render_results_section(results: list[TopicResult]) -> ScriptBuilderPayload | None:
    st.subheader("Results")
    saved_topic: ScriptBuilderPayload | None = None
    for i, topic in enumerate(results):
        payload = render_topic_card(topic, i)
        if payload is not None:
            saved_topic = payload
    return saved_topic
