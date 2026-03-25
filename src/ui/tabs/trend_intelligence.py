from __future__ import annotations

import streamlit as st

from src.trend_intelligence import TrendIntelligenceService


@st.cache_resource(show_spinner=False)
def _trend_service() -> TrendIntelligenceService:
    return TrendIntelligenceService()


def _render_topic_card(topic, rank: int) -> None:
    with st.container(border=True):
        st.subheader(f"#{rank} {topic.title}")
        st.metric("Total Score", f"{topic.score.total:.2f}")

        a, b, c, d, e = st.columns(5)
        a.metric("Momentum", f"{topic.score.momentum:.2f}")
        b.metric("Watch-Time", f"{topic.score.watch_time:.2f}")
        c.metric("Clickability", f"{topic.score.clickability:.2f}")
        d.metric("Competition Gap", f"{topic.score.competition_gap:.2f}")
        e.metric("Brand Fit", f"{topic.score.brand_alignment:.2f}")

        st.caption(f"Why trending: {topic.why_trending}")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("**Suggested content angles**")
            for angle in topic.content_angles:
                st.markdown(f"- {angle}")
        with col2:
            st.markdown("**Suggested hooks**")
            for hook in topic.hooks:
                st.markdown(f"- {hook}")
        with col3:
            st.markdown("**Thumbnail ideas**")
            for idea in topic.thumbnail_ideas:
                st.markdown(f"- {idea}")

        if st.button("Save to Script Pipeline", key=f"save_topic_{rank}_{topic.title}", use_container_width=True):
            st.session_state.topic = topic.title
            st.session_state.project_title = topic.title
            st.success(f"Saved '{topic.title}' into script pipeline.")


def tab_trend_intelligence(active_project_id: str) -> None:
    st.header("📈 Trend Intelligence")
    st.caption("Find rising history topics with long-watch potential and strong History Crossroads fit.")

    with st.expander("Data sources"):
        st.write("- Google Trends RSS (live)")
        st.write("- YouTube Data API (when YOUTUBE_API_KEY is configured)")
        st.write("- Placeholder adapters are wired for future Perplexity + performance feedback")

    topic_limit = st.slider("Topics per scan", min_value=3, max_value=20, value=8)
    videos_per_topic = st.slider("YouTube videos analyzed per topic", min_value=3, max_value=20, value=10)

    if st.button("Run Trend Scan", type="primary", use_container_width=True):
        with st.spinner("Scanning trend sources and YouTube..."):
            try:
                result = _trend_service().scan(
                    project_id=active_project_id,
                    topic_limit=topic_limit,
                    videos_per_topic=videos_per_topic,
                )
                st.session_state["trend_scan_result"] = result
            except Exception as exc:
                st.error(f"Trend scan failed: {exc}")

    result = st.session_state.get("trend_scan_result")
    if not result:
        st.info("Run a scan to generate ranked topic cards.")
        return

    st.success(f"Scan complete ({result.scan_id[:8]}). Sources: {', '.join(result.sources)}")

    if not result.topics:
        st.warning("No topics found in this scan.")
        return

    for index, topic in enumerate(result.topics, start=1):
        _render_topic_card(topic, index)
