from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID

import streamlit as st

from src.trend_intelligence.pipeline_service import TrendIntelligencePipelineService
from src.trend_intelligence.repository import TrendIntelligencePersistenceError, TrendIntelligenceRepository
from src.trend_intelligence.types import TrendScanFilters as ServiceTrendScanFilters
from src.ui.state import active_project_id, save_project_state
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

LOCAL_DEV_USER_UUID = "00000000-0000-0000-0000-000000000001"


def _resolve_user_id() -> str:
    # Local fallback must be a UUID so inserts/filters against UUID user_id columns do not fail.
    candidate = str(st.session_state.get("trend_intelligence_user_id") or "").strip()
    if not candidate:
        return LOCAL_DEV_USER_UUID
    try:
        return str(UUID(candidate))
    except (ValueError, TypeError, AttributeError):
        return LOCAL_DEV_USER_UUID


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


def _validate_trend_persistence_at_startup(repo: TrendIntelligenceRepository) -> tuple[bool, str | None]:
    validation = repo.validate_required_trend_tables()
    if validation.is_ready:
        return True, None
    return False, validation.admin_message


def _render_trend_persistence_admin_error(admin_message: str) -> None:
    st.error(admin_message)


def tab_trend_intelligence() -> None:
    render_page_header()

    filters = render_filter_panel(DEFAULT_FILTERS)

    st.session_state.setdefault("trend_scan_service", TrendIntelligencePipelineService())
    st.session_state.setdefault("trend_scan_repo", TrendIntelligenceRepository())

    st.session_state.setdefault("trend_scan_has_run", False)
    st.session_state.setdefault("trend_scan_results", [])
    st.session_state.setdefault("trend_scan_error", None)
    st.session_state.setdefault("trend_scan_warnings", [])
    st.session_state.setdefault("trend_scan_last_summary", None)
    st.session_state.setdefault("trend_scan_last_run_id", None)
    st.session_state.setdefault("trend_scan_topic_result_ids", {})
    st.session_state.setdefault("trend_scan_persistence_ready", None)
    st.session_state.setdefault("trend_scan_persistence_error", None)

    if st.session_state.trend_scan_persistence_ready is None:
        ready, admin_error = _validate_trend_persistence_at_startup(st.session_state.trend_scan_repo)
        st.session_state.trend_scan_persistence_ready = ready
        st.session_state.trend_scan_persistence_error = admin_error

    persistence_ready = bool(st.session_state.trend_scan_persistence_ready)
    persistence_error = st.session_state.trend_scan_persistence_error
    if not persistence_ready and persistence_error:
        _render_trend_persistence_admin_error(persistence_error)

    if st.button("Run Scan", type="primary", use_container_width=True):
        st.session_state.trend_scan_has_run = True
        st.session_state.trend_scan_error = None
        st.session_state.trend_scan_warnings = []
        st.session_state.trend_scan_topic_result_ids = {}

        service_filters = ServiceTrendScanFilters(
            timeframe=filters.timeframe,
            content_type=filters.content_type,
            brand_focus=filters.brand_focus,
            minimum_score=filters.min_score,
        )

        scan_run_id: str | None = None
        if persistence_ready:
            try:
                scan_run_id = st.session_state.trend_scan_repo.create_scan_run(
                    user_id=_resolve_user_id(),
                    filters_json=asdict(service_filters),
                )
            except TrendIntelligencePersistenceError as exc:
                st.session_state.trend_scan_persistence_ready = False
                st.session_state.trend_scan_persistence_error = str(exc)
                persistence_ready = False
                _render_trend_persistence_admin_error(str(exc))
        st.session_state.trend_scan_last_run_id = scan_run_id

        with st.status("Running trend scan...", expanded=True) as status:
            st.write("Collecting topic signals from configured sources...")
            st.write("Analyzing topic momentum and content fit...")
            try:
                execution = st.session_state.trend_scan_service.run_trend_intelligence_scan_with_status(service_filters)
                if persistence_ready and scan_run_id:
                    st.session_state.trend_scan_topic_result_ids = st.session_state.trend_scan_repo.save_topic_results(
                        scan_run_id=scan_run_id,
                        topics=list(execution.topics),
                    )
                else:
                    st.session_state.trend_scan_topic_result_ids = {}
                results = _to_ui_topic_results(execution.topics)
                st.session_state.trend_scan_results = results
                st.session_state.trend_scan_warnings = [warning.message for warning in execution.warnings]

                scanned_at = execution.scanned_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
                top_topic = results[0].topic_title if results else "None"
                summary = {
                    "scanned_at": scanned_at,
                    "topic_count": len(results),
                    "top_topic": top_topic,
                    "warning_count": len(execution.warnings),
                }
                st.session_state.trend_scan_last_summary = summary
                if persistence_ready and scan_run_id:
                    st.session_state.trend_scan_repo.complete_scan_run(
                        scan_run_id=scan_run_id,
                        status="completed",
                        summary_json=summary,
                    )
                status.update(label="Scan complete", state="complete")
            except TrendIntelligencePersistenceError as exc:
                st.session_state.trend_scan_persistence_ready = False
                st.session_state.trend_scan_persistence_error = str(exc)
                st.session_state.trend_scan_error = (
                    "Scan finished, but persistence failed due to a Trend Intelligence Supabase setup issue."
                )
                status.update(label="Scan completed with persistence warning", state="error")
            except Exception as exc:
                st.session_state.trend_scan_results = []
                st.session_state.trend_scan_error = str(exc)
                summary = {
                    "scanned_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "topic_count": 0,
                    "top_topic": "None",
                    "error": str(exc),
                }
                st.session_state.trend_scan_last_summary = summary
                if persistence_ready and scan_run_id:
                    st.session_state.trend_scan_repo.complete_scan_run(
                        scan_run_id=scan_run_id,
                        status="failed",
                        summary_json=summary,
                    )
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

    selected_for_pipeline = render_results_section(st.session_state.trend_scan_results)
    if selected_for_pipeline is not None:
        topic_result_id = st.session_state.trend_scan_topic_result_ids.get(selected_for_pipeline.topic_title)
        try:
            candidate_id = st.session_state.trend_scan_repo.save_topic_candidate(
                user_id=_resolve_user_id(),
                topic_title=selected_for_pipeline.topic_title,
                source_topic_result_id=topic_result_id,
                notes="Saved from Trend Intelligence",
                status="saved",
            )
        except TrendIntelligencePersistenceError:
            candidate_id = None
            _render_trend_persistence_admin_error(
                "Could not save this topic to Supabase due to a Trend Intelligence setup/access issue."
            )
        st.session_state.topic = selected_for_pipeline.topic_title
        st.session_state.project_title = selected_for_pipeline.topic_title
        st.session_state.story_angle = selected_for_pipeline.preferred_content_angle
        st.session_state.video_description_direction = selected_for_pipeline.selected_hook
        st.session_state.trend_script_context = {
            "topic_title": selected_for_pipeline.topic_title,
            "why_may_be_trending": selected_for_pipeline.why_may_be_trending,
            "preferred_content_angle": selected_for_pipeline.preferred_content_angle,
            "selected_hook": selected_for_pipeline.selected_hook,
            "thumbnail_direction": selected_for_pipeline.thumbnail_direction,
            "score_breakdown": asdict(selected_for_pipeline.score_breakdown),
            "source_topic_result_id": topic_result_id,
            "source_scan_run_id": st.session_state.get("trend_scan_last_run_id"),
            "saved_topic_candidate_id": candidate_id,
        }

        script_job_id = st.session_state.trend_scan_repo.save_script_builder_job(
            user_id=_resolve_user_id(),
            project_id=active_project_id(),
            topic_title=selected_for_pipeline.topic_title,
            why_may_be_trending=selected_for_pipeline.why_may_be_trending,
            preferred_content_angle=selected_for_pipeline.preferred_content_angle,
            selected_hook=selected_for_pipeline.selected_hook,
            thumbnail_direction=selected_for_pipeline.thumbnail_direction,
            score_breakdown_json=asdict(selected_for_pipeline.score_breakdown),
            source_topic_result_id=topic_result_id,
            source_scan_run_id=st.session_state.get("trend_scan_last_run_id"),
            saved_topic_candidate_id=candidate_id,
        )
        save_project_state(active_project_id())
        if candidate_id is None:
            st.warning(f"Saved '{selected_for_pipeline.topic_title}' locally, but Supabase persistence is unavailable.")
        elif script_job_id is None:
            st.success(f"Sent '{selected_for_pipeline.topic_title}' to Script Builder. Bridge job will persist when Supabase is available.")
        else:
            st.success(
                f"Sent '{selected_for_pipeline.topic_title}' to Script Builder "
                f"(candidate #{candidate_id}, script job #{script_job_id})."
            )
