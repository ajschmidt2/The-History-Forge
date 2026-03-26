from __future__ import annotations

from src.trend_intelligence.repository import TrendIntelligenceRepository


class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client: "_FakeClient", table_name: str):
        self.client = client
        self.table_name = table_name
        self._op = "select"
        self._insert_payload = None
        self._filters: list[tuple[str, object]] = []

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._insert_payload = payload
        return self

    def eq(self, key, value):
        self._filters.append((key, value))
        return self

    def limit(self, _value):
        return self

    def execute(self):
        if self._op == "insert":
            self.client.inserts.append((self.table_name, self._insert_payload))
            if self.table_name == "trend_topic_script_jobs":
                row = dict(self._insert_payload)
                row["id"] = self.client.next_bridge_id
                self.client.next_bridge_id += 1
                self.client.bridge_rows.append(row)
            return _Response([self._insert_payload])

        if self.table_name == "information_schema.columns":
            table_name = ""
            for key, value in self._filters:
                if key == "table_name":
                    table_name = str(value)
            cols = self.client.table_columns.get(table_name, set())
            return _Response([{"column_name": c} for c in cols])

        if self.table_name == "trend_topic_script_jobs":
            rows = list(self.client.bridge_rows)
            for key, value in self._filters:
                rows = [row for row in rows if row.get(key) == value]
            return _Response(rows)

        return _Response([])


class _FakeClient:
    def __init__(self, table_columns: dict[str, set[str]] | None = None):
        self.table_columns = table_columns or {}
        self.inserts: list[tuple[str, object]] = []
        self.bridge_rows: list[dict[str, object]] = []
        self.next_bridge_id = 1

    def table(self, name: str):
        return _Query(self, name)


def _repo_with_fake_client(fake_client: _FakeClient) -> TrendIntelligenceRepository:
    repo = TrendIntelligenceRepository()
    repo._client = fake_client
    return repo


def test_save_script_builder_job_creates_bridge_and_returns_id():
    repo = _repo_with_fake_client(_FakeClient())

    bridge_id = repo.save_script_builder_job(
        user_id="u-1",
        project_id="project-1",
        topic_title="Bronze Age Collapse",
        why_may_be_trending="Renewed interest in collapse narratives",
        preferred_content_angle="Systems-failure timeline",
        selected_hook="One chain reaction changed history",
        thumbnail_direction="Ruins over world map",
        score_breakdown_json={"trend_momentum_score": 88},
        source_topic_result_id=11,
        source_scan_run_id="run-1",
        saved_topic_candidate_id=7,
    )

    assert bridge_id == 1
    assert any(table == "trend_topic_script_jobs" for table, _ in repo._client.inserts)


def test_save_script_builder_job_also_inserts_into_script_jobs_when_table_exists():
    fake = _FakeClient(
        table_columns={
            "script_jobs": {"project_id", "topic", "status", "topic_context_json", "trend_topic_script_job_id"}
        }
    )
    repo = _repo_with_fake_client(fake)

    repo.save_script_builder_job(
        user_id="u-1",
        project_id="project-1",
        topic_title="Bronze Age Collapse",
        why_may_be_trending="Renewed interest in collapse narratives",
        preferred_content_angle="Systems-failure timeline",
        selected_hook="One chain reaction changed history",
        thumbnail_direction="Ruins over world map",
        score_breakdown_json={"trend_momentum_score": 88},
        source_topic_result_id=11,
        source_scan_run_id="run-1",
        saved_topic_candidate_id=7,
    )

    script_inserts = [payload for table, payload in fake.inserts if table == "script_jobs"]
    assert len(script_inserts) == 1
    assert script_inserts[0]["project_id"] == "project-1"
    assert script_inserts[0]["topic"] == "Bronze Age Collapse"
