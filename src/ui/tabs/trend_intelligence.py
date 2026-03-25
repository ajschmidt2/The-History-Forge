from __future__ import annotations

import streamlit as st

from src.trend_intelligence.models import TrendScanFilters
from src.ui.tabs.trend_intelligence_components import (
    TrendScanUiState,
    mocked_topic_results,
    render_filter_panel,
    render_page_intro,
    render_results,
)


def tab_trend_intelligence(active_project_id: str) -> None:
    del active_project_id  # Reserved for real backend scan wiring.

    render_page_intro()

    default_filters = TrendScanFilters(
        timeframe="7d",
        content_type="both",
        brand_focus="all",
        minimum_score=50,
    )

    selected_filters = render_filter_panel(default_filters)
    st.session_state["trend_scan_filters"] = selected_filters

    if "trend_ui_state" not in st.session_state:
        st.session_state["trend_ui_state"] = TrendScanUiState(is_loading=False, error_message=None, has_run=False)
    if "trend_scan_results" not in st.session_state:
        st.session_state["trend_scan_results"] = []

    trigger_scan = st.button("Run Scan", type="primary", use_container_width=True)
    if trigger_scan:
        st.session_state["trend_ui_state"] = TrendScanUiState(is_loading=True, error_message=None, has_run=True)
        try:
            # Temporary mocked path. This block is intentionally structured like a real API call.
            if selected_filters.timeframe == "24h" and selected_filters.brand_focus == "mysteries":
                raise RuntimeError("Sample upstream timeout while fetching mystery trend feed")

            st.session_state["trend_scan_results"] = mocked_topic_results()
            st.session_state["trend_ui_state"] = TrendScanUiState(is_loading=False, error_message=None, has_run=True)
        except Exception as exc:
            st.session_state["trend_scan_results"] = []
            st.session_state["trend_ui_state"] = TrendScanUiState(
                is_loading=False,
                error_message=str(exc),
                has_run=True,
            )

    render_results(
        results=st.session_state.get("trend_scan_results", []),
        ui_state=st.session_state["trend_ui_state"],
    )
