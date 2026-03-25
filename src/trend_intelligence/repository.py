from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import src.supabase_storage as _sb_store
from src.trend_intelligence.models import RankedTopic
from src.trend_intelligence.types import TopicResult


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
        self._client.table("trend_scan_runs").insert(payload).execute()
        return run_id

    def complete_scan_run(self, *, scan_run_id: str, status: str, summary_json: dict[str, Any]) -> None:
        if self._client is None:
            return

        payload = {
            "status": status,
            "summary_json": summary_json,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        self._client.table("trend_scan_runs").update(payload).eq("id", scan_run_id).execute()

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

        resp = self._client.table("trend_topic_results").insert(rows).execute()
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
        resp = self._client.table("saved_topic_candidates").insert(payload).execute()
        row = (resp.data or [{}])[0]
        candidate_id = row.get("id")
        return candidate_id if isinstance(candidate_id, int) else None
