from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import src.supabase_storage as _sb_store
from src.trend_intelligence.models import RankedTopic
from src.trend_intelligence.persistence_validation import (
    TrendPersistenceValidationResult,
    build_trend_persistence_admin_message,
    check_trend_intelligence_setup,
    classify_trend_setup_error,
    looks_like_schema_error,
)
from src.trend_intelligence.types import TopicResult


class TrendIntelligencePersistenceError(RuntimeError):
    pass


class TrendIntelligenceRepository:
    LOCAL_DEV_USER_UUID = "00000000-0000-0000-0000-000000000001"

    def __init__(self) -> None:
        self._client = _sb_store.get_client()

    def _normalize_user_id(self, user_id: str | None) -> str:
        candidate = str(user_id or "").strip()
        if not candidate:
            return self.LOCAL_DEV_USER_UUID
        try:
            return str(UUID(candidate))
        except (ValueError, TypeError, AttributeError):
            return self.LOCAL_DEV_USER_UUID

    # Legacy tables used by the older TrendIntelligenceService
    def create_scan(self, *, project_id: str, source_names: list[str], status: str) -> str:
        scan_id = uuid4().hex
        if self._client is None:
            return scan_id
        payload = {
            "id": scan_id,
            "project_id": project_id,
            "source_names": source_names,
            "status": status,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._client.table("trend_intelligence_scans").insert(payload).execute()
        return scan_id

    def update_scan_status(self, scan_id: str, status: str, error_message: str | None = None) -> None:
        if self._client is None:
            return
        payload: dict[str, Any] = {"status": status, "updated_at": datetime.now(UTC).isoformat()}
        if error_message:
            payload["error_message"] = error_message
        self._client.table("trend_intelligence_scans").update(payload).eq("id", scan_id).execute()

    def save_topics(self, scan_id: str, ranked_topics: list[RankedTopic]) -> None:
        if self._client is None:
            return
        rows = [topic.as_db_payload(scan_id) for topic in ranked_topics]
        if not rows:
            return
        self._client.table("trend_intelligence_topics").insert(rows).execute()

    def get_recent_topics(self, *, project_id: str, limit: int = 25) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        query = (
            self._client.table("trend_intelligence_topics")
            .select("*, trend_intelligence_scans!inner(project_id, created_at)")
            .eq("trend_intelligence_scans.project_id", project_id)
            .order("total_score", desc=True)
            .limit(limit)
        )
        resp = query.execute()
        return resp.data or []

    # New persistence model for the pipeline-based Trend Intelligence UI
    def create_scan_run(self, *, user_id: str, filters_json: dict[str, Any]) -> str:
        run_id = uuid4().hex
        if self._client is None:
            return run_id

        payload = {
            "id": run_id,
            "user_id": self._normalize_user_id(user_id),
            "filters_json": filters_json,
            "started_at": datetime.now(UTC).isoformat(),
            "status": "running",
            "summary_json": {},
        }
        try:
            resp = self._client.table("trend_scan_runs").insert(payload).execute()
            self._raise_if_schema_response(resp, table_name="trend_scan_runs")
        except Exception as exc:
            self._raise_if_schema_error(exc, table_name="trend_scan_runs")
        return run_id

    def complete_scan_run(self, *, scan_run_id: str, status: str, summary_json: dict[str, Any]) -> None:
        if self._client is None:
            return

        payload = {
            "status": status,
            "summary_json": summary_json,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        try:
            resp = self._client.table("trend_scan_runs").update(payload).eq("id", scan_run_id).execute()
            self._raise_if_schema_response(resp, table_name="trend_scan_runs")
        except Exception as exc:
            self._raise_if_schema_error(exc, table_name="trend_scan_runs")

    def save_topic_results(self, *, scan_run_id: str, topics: list[TopicResult]) -> dict[str, int]:
        if self._client is None or not topics:
            return {}

        rows: list[dict[str, Any]] = []
        for topic in topics:
            rows.append(
                {
                    "scan_run_id": scan_run_id,
                    "topic_title": topic.topic,
                    "score_total": topic.score.overall,
                    "score_breakdown_json": asdict(topic.score),
                    "insight_json": asdict(topic.insight),
                    "source_json": {
                        "source": topic.source,
                        "sampled_videos": [asdict(video) for video in topic.sampled_videos],
                    },
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )

        try:
            resp = self._client.table("trend_topic_results").insert(rows).execute()
            self._raise_if_schema_response(resp, table_name="trend_topic_results")
        except Exception as exc:
            self._raise_if_schema_error(exc, table_name="trend_topic_results")
            return {}
        data = resp.data or []
        id_map: dict[str, int] = {}
        for row in data:
            topic_title = str(row.get("topic_title", "") or "")
            row_id = row.get("id")
            if topic_title and isinstance(row_id, int):
                id_map[topic_title] = row_id
        return id_map

    def save_topic_candidate(
        self,
        *,
        user_id: str,
        topic_title: str,
        source_topic_result_id: int | None,
        notes: str = "",
        status: str = "saved",
    ) -> int | None:
        if self._client is None:
            return None

        payload = {
            "user_id": self._normalize_user_id(user_id),
            "topic_title": topic_title,
            "source_topic_result_id": source_topic_result_id,
            "notes": notes,
            "status": status,
            "created_at": datetime.now(UTC).isoformat(),
        }
        try:
            resp = self._client.table("saved_topic_candidates").insert(payload).execute()
            self._raise_if_schema_response(resp, table_name="saved_topic_candidates")
        except Exception as exc:
            self._raise_if_schema_error(exc, table_name="saved_topic_candidates")
            return None
        row = (resp.data or [{}])[0]
        candidate_id = row.get("id")
        return candidate_id if isinstance(candidate_id, int) else None

    def validate_required_trend_tables(self) -> TrendPersistenceValidationResult:
        check = check_trend_intelligence_setup(self._client)
        details = check.get("details") if isinstance(check, dict) else {}
        missing_tables = tuple(details.get("missing_tables", ())) if isinstance(details, dict) else ()
        return TrendPersistenceValidationResult(
            is_ready=bool(check.get("ok")),
            status=str(check.get("status", "connection_error")),
            missing_tables=missing_tables,
            details=details if isinstance(details, dict) else {},
        )

    def _raise_if_schema_error(self, exc: Exception, *, table_name: str) -> None:
        if looks_like_schema_error(exc):
            status = classify_trend_setup_error(str(exc))
            raise TrendIntelligencePersistenceError(
                build_trend_persistence_admin_message(
                    status=status,
                    details={"table": table_name, "error": str(exc)},
                )
            ) from exc
        raise

    def _raise_if_schema_response(self, resp: Any, *, table_name: str) -> None:
        raw_error = getattr(resp, "error", None)
        if raw_error and looks_like_schema_error(Exception(str(raw_error))):
            raise TrendIntelligencePersistenceError(
                build_trend_persistence_admin_message(
                    status=classify_trend_setup_error(str(raw_error)),
                    details={"table": table_name, "error": str(raw_error)},
                )
            )

        data = getattr(resp, "data", None)
        if isinstance(data, dict):
            err_fragments = [str(data.get("code", "")), str(data.get("message", "")), str(data.get("hint", ""))]
            error_blob = " | ".join(fragment for fragment in err_fragments if fragment and fragment != "None")
            if error_blob and looks_like_schema_error(Exception(error_blob)):
                raise TrendIntelligencePersistenceError(
                    build_trend_persistence_admin_message(
                        status=classify_trend_setup_error(error_blob),
                        details={"table": table_name, "error": error_blob},
                    )
                )

    def save_script_builder_job(
        self,
        *,
        user_id: str,
        project_id: str,
        topic_title: str,
        why_may_be_trending: str,
        preferred_content_angle: str,
        selected_hook: str,
        thumbnail_direction: str,
        score_breakdown_json: dict[str, Any],
        source_topic_result_id: int | None,
        source_scan_run_id: str | None,
        saved_topic_candidate_id: int | None,
    ) -> int | None:
        if self._client is None:
            return None

        now_iso = datetime.now(UTC).isoformat()
        normalized_user_id = self._normalize_user_id(user_id)
        bridge_payload = {
            "user_id": normalized_user_id,
            "project_id": project_id,
            "topic_title": topic_title,
            "why_may_be_trending": why_may_be_trending,
            "preferred_content_angle": preferred_content_angle,
            "selected_hook": selected_hook,
            "thumbnail_direction": thumbnail_direction,
            "score_breakdown_json": score_breakdown_json,
            "source_topic_result_id": source_topic_result_id,
            "source_scan_run_id": source_scan_run_id,
            "saved_topic_candidate_id": saved_topic_candidate_id,
            "status": "queued",
            "created_at": now_iso,
        }

        try:
            self._client.table("trend_topic_script_jobs").insert(bridge_payload).execute()
        except Exception:
            return None

        bridge_row = (
            self._client.table("trend_topic_script_jobs")
            .select("id")
            .eq("user_id", normalized_user_id)
            .eq("project_id", project_id)
            .eq("topic_title", topic_title)
            .eq("created_at", now_iso)
            .limit(1)
            .execute()
        )
        rows = bridge_row.data or []
        bridge_id = rows[0].get("id") if rows and isinstance(rows[0], dict) else None

        script_payload = {
            "project_id": project_id,
            "topic_title": topic_title,
            "status": "queued",
            "source_topic_result_id": source_topic_result_id,
            "source_scan_run_id": source_scan_run_id,
            "trend_topic_script_job_id": bridge_id,
            "topic_context_json": {
                "why_may_be_trending": why_may_be_trending,
                "preferred_content_angle": preferred_content_angle,
                "selected_hook": selected_hook,
                "thumbnail_direction": thumbnail_direction,
                "score_breakdown_json": score_breakdown_json,
            },
        }
        try:
            self._client.table("script_jobs").insert(script_payload).execute()
        except Exception:
            pass

        return bridge_id if isinstance(bridge_id, int) else None
