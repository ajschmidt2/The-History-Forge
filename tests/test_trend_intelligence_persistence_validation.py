from __future__ import annotations

from src.trend_intelligence.persistence_validation import check_trend_intelligence_setup, classify_trend_setup_error


class _Response:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error


class _Query:
    def __init__(self, client: "_FakeClient", table_name: str):
        self.client = client
        self.table_name = table_name

    def select(self, *_args, **_kwargs):
        return self

    def limit(self, _value):
        return self

    def execute(self):
        error = self.client.error_tables.get(self.table_name)
        if error:
            raise RuntimeError(error)
        response_error = self.client.response_errors.get(self.table_name)
        if response_error:
            return _Response(error=response_error)
        return _Response(data=[])


class _FakeClient:
    def __init__(
        self,
        error_tables: dict[str, str] | None = None,
        response_errors: dict[str, str] | None = None,
    ):
        self.error_tables = error_tables or {}
        self.response_errors = response_errors or {}

    def table(self, name: str):
        return _Query(self, name)


def test_classify_trend_setup_error_rules():
    assert classify_trend_setup_error("public.information_schema.columns") == "schema_cache"
    assert classify_trend_setup_error('relation "x" does not exist') == "missing_tables"
    assert classify_trend_setup_error("RLS policy denied") == "permission_error"
    assert classify_trend_setup_error("timeout") == "connection_error"


def test_check_trend_intelligence_setup_ready():
    result = check_trend_intelligence_setup(_FakeClient())

    assert result["ok"] is True
    assert result["status"] == "ready"


def test_check_trend_intelligence_setup_missing_table():
    result = check_trend_intelligence_setup(
        _FakeClient(error_tables={"trend_topic_results": 'Could not find the table "trend_topic_results"'})
    )

    assert result["ok"] is False
    assert result["status"] == "missing_tables"


def test_check_trend_intelligence_setup_response_error_is_classified():
    result = check_trend_intelligence_setup(
        _FakeClient(response_errors={"trend_scan_runs": 'Could not find the table "trend_scan_runs"'})
    )

    assert result["ok"] is False
    assert result["status"] == "missing_tables"
