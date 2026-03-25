from __future__ import annotations

import time

import streamlit as st

from src.ui.trend_intelligence_components import (
    render_filter_panel,
    render_page_header,
    render_results_section,
)
from src.ui.trend_intelligence_mock_data import SAMPLE_TOPIC_RESULTS
from src.ui.trend_intelligence_types import TopicResult, TrendScanFilters


DEFAULT_FILTERS = TrendScanFilters(
    timeframe="7d",
    content_type="both",
    brand_focus="all",
    min_score=65,
)


def _run_mock_scan(filters: TrendScanFilters, simulate_error: bool) -> list[TopicResult]:
    _ = filters  # Placeholder for future backend call inputs
    time.sleep(0.8)
    if simulate_error:
        raise RuntimeError("Scan service unavailable. Please retry in a moment.")
    return SAMPLE_TOPIC_RESULTS


def tab_trend_intelligence() -> None:
    render_page_header()

    filters, simulate_error = render_filter_panel(DEFAULT_FILTERS)

    st.session_state.setdefault("trend_scan_has_run", False)
    st.session_state.setdefault("trend_scan_results", [])
    st.session_state.setdefault("trend_scan_error", None)

    if st.button("Run Scan", type="primary", use_container_width=True):
        st.session_state.trend_scan_has_run = True
        st.session_state.trend_scan_error = None

        with st.status("Running trend scan...", expanded=True) as status:
            st.write("Collecting topic signals...")
            st.write("Scoring trend momentum and content fit...")
            try:
                results = _run_mock_scan(filters, simulate_error)
                st.session_state.trend_scan_results = results
                status.update(label="Scan complete", state="complete")
            except Exception as exc:
                st.session_state.trend_scan_results = []
                st.session_state.trend_scan_error = str(exc)
                status.update(label="Scan failed", state="error")

    st.divider()

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

    render_results_section(st.session_state.trend_scan_results)
