from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from src.trend_intelligence.pipeline_service import TrendIntelligencePipelineService
from src.trend_intelligence.types import TrendScanFilters as ServiceTrendScanFilters
from src.ui.trend_intelligence_components import render_filter_panel, render_page_header, render_results_section
from src.ui.trend_intelligence_types import (
    TopicInsight,
    TopicResult,
    TopicScoreBreakdown,
    TrendScanFilters,
)


DEFAULT_FILTERS = TrendScanFilters(
    timeframe="7d",
    content_type="both",
    brand_focus="all",
    min_score=65,
)


def _to_ui_topic_results(service_results) -> list[TopicResult]:
    mapped: list[TopicResult] = []
    for item in service_results:
        mapped.append(
            TopicResult(
                topic_title=item.topic,
                total_score=int(round(item.score.overall)),
                score_breakdown=TopicScoreBreakdown(
                    trend_momentum_score=int(round(item.score.trend_momentum)),
                    watch_time_potential_score=int(round(item.score.watch_time_potential)),
                    clickability_score=int(round(item.score.clickability)),
                    competition_gap_score=int(round(item.score.competition_gap)),
                    brand_alignment_score=int(round(item.score.brand_alignment)),
                ),
                insight=TopicInsight(
                    reasoning=item.insight.why_now,
                    content_angle_ideas=list(item.insight.opportunities),
                    hook_ideas=list(item.insight.risks),
                    thumbnail_ideas=[],
                ),
            )
        )
    return mapped


def _render_last_scan_summary() -> None:
    summary = st.session_state.get("trend_scan_last_summary")
    if not summary:
        return

    st.subheader("Last scan")
    cols = st.columns(3)
    cols[0].metric("Date/Time", summary["scanned_at"])
    cols[1].metric("Topics found", summary["topic_count"])
    cols[2].metric("Top scoring topic", summary["top_topic"])


def tab_trend_intelligence() -> None:
    render_page_header()

    filters = render_filter_panel(DEFAULT_FILTERS)

    st.session_state.setdefault("trend_scan_service", TrendIntelligencePipelineService())

    st.session_state.setdefault("trend_scan_has_run", False)
    st.session_state.setdefault("trend_scan_results", [])
    st.session_state.setdefault("trend_scan_error", None)
    st.session_state.setdefault("trend_scan_warnings", [])
    st.session_state.setdefault("trend_scan_last_summary", None)

    if st.button("Run Scan", type="primary", use_container_width=True):
        st.session_state.trend_scan_has_run = True
        st.session_state.trend_scan_error = None
        st.session_state.trend_scan_warnings = []

        with st.status("Running trend scan...", expanded=True) as status:
            st.write("Collecting topic signals from configured sources...")
            st.write("Analyzing topic momentum and content fit...")
            try:
                service_filters = ServiceTrendScanFilters(
                    timeframe=filters.timeframe,
                    content_type=filters.content_type,
                    brand_focus=filters.brand_focus,
                    minimum_score=filters.min_score,
                )
                execution = st.session_state.trend_scan_service.run_trend_intelligence_scan_with_status(service_filters)
                results = _to_ui_topic_results(execution.topics)
                st.session_state.trend_scan_results = results
                st.session_state.trend_scan_warnings = [warning.message for warning in execution.warnings]

                scanned_at = execution.scanned_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
                top_topic = results[0].topic_title if results else "None"
                st.session_state.trend_scan_last_summary = {
                    "scanned_at": scanned_at,
                    "topic_count": len(results),
                    "top_topic": top_topic,
                }
                status.update(label="Scan complete", state="complete")
            except Exception as exc:
                st.session_state.trend_scan_results = []
                st.session_state.trend_scan_error = str(exc)
                st.session_state.trend_scan_last_summary = {
                    "scanned_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "topic_count": 0,
                    "top_topic": "None",
                }
                status.update(label="Scan failed", state="error")

    st.divider()
    _render_last_scan_summary()

    if not st.session_state.trend_scan_has_run:
        st.subheader("Results")
        st.info("No scan results yet. Set filters and click **Run Scan** to start.")
        return

    if st.session_state.trend_scan_error:
        st.subheader("Results")
        st.error(f"Unable to load trend results: {st.session_state.trend_scan_error}")
        return

    if not st.session_state.trend_scan_results:
        st.subheader("Results")
        st.warning("Scan finished, but no topics matched your current filters.")
        return

    if st.session_state.trend_scan_warnings:
        for warning in st.session_state.trend_scan_warnings:
            st.warning(f"Partial source failure: {warning}")

    render_results_section(st.session_state.trend_scan_results)
