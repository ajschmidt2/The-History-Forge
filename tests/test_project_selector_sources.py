import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ui import state


def test_available_project_ids_merges_local_and_supabase(monkeypatch) -> None:
    monkeypatch.setattr(state, "_existing_project_ids", lambda: ["local-project", "shared-project"])
    monkeypatch.setattr(
        state,
        "_supabase_project_ids",
        lambda: ["remote-project", "shared-project", "another-remote"],
    )

    assert state._available_project_ids() == ["another-remote", "local-project", "remote-project", "shared-project"]


def test_supabase_project_ids_slugifies_and_ignores_invalid(monkeypatch) -> None:
    monkeypatch.setattr(
        state._sb_store,
        "list_projects",
        lambda: [
            {"id": "hc_2026_01_28_demo"},
            {"title": "History Project"},
            {"id": ""},
            {},
            "bad-record",
        ],
    )

    assert state._supabase_project_ids() == ["hc-2026-01-28-demo", "history-project"]
