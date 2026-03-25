from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import src.supabase_storage as _sb_store
from src.trend_intelligence.models import RankedTopic


class TrendIntelligenceRepository:
    def __init__(self) -> None:
        self._client = _sb_store.get_client()

    def create_scan(self, *, project_id: str, source_names: list[str], status: str) -> str:
        scan_id = uuid4().hex
        if self._client is None:
            return scan_id
        payload = {
            "id": scan_id,
            "project_id": project_id,
            "source_names": source_names,
            "status": status,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._client.table("trend_intelligence_scans").insert(payload).execute()
        return scan_id

    def update_scan_status(self, scan_id: str, status: str, error_message: str | None = None) -> None:
        if self._client is None:
            return
        payload: dict[str, Any] = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
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
