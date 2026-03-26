from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import src.supabase_storage as _sb_store
from src.trend_intelligence.models import RankedTopic
from src.trend_intelligence.persistence_validation import (
    REQUIRED_TREND_TABLES,
    TrendPersistenceValidationResult,
    looks_like_schema_error,
)
from src.trend_intelligence.types import TopicResult


class TrendIntelligencePersistenceError(RuntimeError):
    pass


class TrendIntelligenceRepository:
    def __init__(self) -> None:
        self._client = _sb_store.get_client()

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
            "user_id": user_id,
            "filters_json": filters_json,
            "started_at": datetime.now(UTC).isoformat(),
            "status": "running",
            "summary_json": {},
        }
        try:
            self._client.table("trend_scan_runs").insert(payload).execute()
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
            self._client.table("trend_scan_runs").update(payload).eq("id", scan_run_id).execute()
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
        except Exception as exc:
            self._raise_if_schema_error(exc, table_name="trend_topic_results")
            return {}
        data = resp.data or []
        id_map: dict[str, int] = {}
        for row in data:
            title = str(row.get("topic_title", "") or "")
            row_id = row.get("id")
            if title and isinstance(row_id, int):
                id_map[title] = row_id
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
            "user_id": user_id,
            "topic_title": topic_title,
            "source_topic_result_id": source_topic_result_id,
            "notes": notes,
            "status": status,
            "created_at": datetime.now(UTC).isoformat(),
        }
        try:
            resp = self._client.table("saved_topic_candidates").insert(payload).execute()
        except Exception as exc:
            self._raise_if_schema_error(exc, table_name="saved_topic_candidates")
            return None
        row = (resp.data or [{}])[0]
        candidate_id = row.get("id")
        return candidate_id if isinstance(candidate_id, int) else None

    def validate_required_trend_tables(self) -> TrendPersistenceValidationResult:
        if self._client is None:
            return TrendPersistenceValidationResult(is_ready=False, schema_errors=("Supabase client is unavailable",))

        missing: list[str] = []
        schema_errors: list[str] = []
        for table_name in REQUIRED_TREND_TABLES:
            try:
                columns = self._table_columns(table_name)
            except Exception as exc:
                if looks_like_schema_error(exc):
                    schema_errors.append(f"{table_name}: {exc}")
                    continue
                raise
            if not columns:
                missing.append(table_name)

        return TrendPersistenceValidationResult(
            is_ready=not missing and not schema_errors,
            missing_tables=tuple(missing),
            schema_errors=tuple(schema_errors),
        )

    def _table_columns(self, table_name: str) -> set[str]:
        if self._client is None:
            return set()
        resp = (
            self._client.table("information_schema.columns")
            .select("column_name")
            .eq("table_schema", "public")
            .eq("table_name", table_name)
            .execute()
        )
        rows = resp.data or []
        columns: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("column_name", "") or "").strip()
            if name:
                columns.add(name)
        return columns

    def _raise_if_schema_error(self, exc: Exception, *, table_name: str) -> None:
        if looks_like_schema_error(exc):
            raise TrendIntelligencePersistenceError(
                f"Supabase Trend Intelligence table '{table_name}' is missing or has an incompatible schema."
            ) from exc
        raise

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
        bridge_payload = {
            "user_id": user_id,
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
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .eq("topic_title", topic_title)
            .eq("created_at", now_iso)
            .limit(1)
            .execute()
        )
        rows = bridge_row.data or []
        bridge_id = rows[0].get("id") if rows and isinstance(rows[0], dict) else None

        try:
            script_job_columns = self._table_columns("script_jobs")
        except Exception:
            script_job_columns = set()
        if script_job_columns:
            script_payload_map = {
                "project_id": project_id,
                "topic_title": topic_title,
                "topic": topic_title,
                "title": topic_title,
                "status": "queued",
                "source_topic_result_id": source_topic_result_id,
                "source_scan_run_id": source_scan_run_id,
                "trend_topic_script_job_id": bridge_id,
                "topic_context_json": {
                    "why_may_be_trending": why_may_be_trending,
                    "preferred_content_angle": preferred_content_angle,
                    "selected_hook": selected_hook,
                    "thumbnail_direction": thumbnail_direction,
                    "score_breakdown": score_breakdown_json,
                },
            }
            script_payload = {key: value for key, value in script_payload_map.items() if key in script_job_columns}
            if script_payload:
                try:
                    self._client.table("script_jobs").insert(script_payload).execute()
                except Exception:
                    pass

        return bridge_id if isinstance(bridge_id, int) else None
