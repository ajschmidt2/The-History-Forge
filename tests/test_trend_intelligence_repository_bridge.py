from __future__ import annotations

from src.trend_intelligence.repository import TrendIntelligencePersistenceError, TrendIntelligenceRepository


class _Response:
    def __init__(self, data, error=None):
        self.data = data
        self.error = error


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
        if self.table_name in self.client.error_tables:
            raise RuntimeError(self.client.error_tables[self.table_name])

        if self._op == "insert":
            self.client.inserts.append((self.table_name, self._insert_payload))
            if self.table_name == "trend_topic_script_jobs":
                row = dict(self._insert_payload)
                row["id"] = self.client.next_bridge_id
                self.client.next_bridge_id += 1
                self.client.bridge_rows.append(row)
            return _Response([self._insert_payload])

        if self.table_name == "trend_topic_script_jobs":
            rows = list(self.client.bridge_rows)
            for key, value in self._filters:
                rows = [row for row in rows if row.get(key) == value]
            return _Response(rows)

        return _Response([])


class _FakeClient:
    def __init__(self, error_tables: dict[str, str] | None = None):
        self.error_tables = error_tables or {}
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


def test_save_script_builder_job_attempts_script_jobs_insert_with_topic_title_payload():
    fake = _FakeClient()
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
    assert script_inserts[0]["topic_title"] == "Bronze Age Collapse"


def test_validate_required_trend_tables_returns_missing_table_list():
    repo = _repo_with_fake_client(
        _FakeClient(
            error_tables={
                "saved_topic_candidates": '42P01: relation "saved_topic_candidates" does not exist',
            }
        )
    )

    result = repo.validate_required_trend_tables()

    assert result.is_ready is False
    assert result.status == "missing_tables"
    assert result.missing_tables == ("saved_topic_candidates",)


def test_create_scan_run_raises_persistence_error_for_missing_table():
    repo = _repo_with_fake_client(
        _FakeClient(
            error_tables={"trend_scan_runs": '42P01: relation "trend_scan_runs" does not exist'},
        )
    )

    try:
        repo.create_scan_run(user_id="u-1", filters_json={})
    except TrendIntelligencePersistenceError:
        pass
    else:
        raise AssertionError("Expected TrendIntelligencePersistenceError")


def test_raise_if_schema_response_handles_response_error_field():
    repo = _repo_with_fake_client(_FakeClient())

    try:
        repo._raise_if_schema_response(  # noqa: SLF001
            _Response([], error='42P01: relation "saved_topic_candidates" does not exist'),
            table_name="saved_topic_candidates",
        )
    except TrendIntelligencePersistenceError:
        pass
    else:
        raise AssertionError("Expected TrendIntelligencePersistenceError")


def test_raise_if_schema_response_ignores_non_classified_dict_error_payload():
    repo = _repo_with_fake_client(_FakeClient())

    repo._raise_if_schema_response(  # noqa: SLF001
        _Response({"code": "42703", "message": 'column "foo" does not exist'}),
        table_name="trend_topic_results",
    )
