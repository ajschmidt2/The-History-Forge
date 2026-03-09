import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ui import state


def test_delete_project_removes_matching_dirs_and_normalized_records(monkeypatch, tmp_path) -> None:
    removed_ids: list[str] = []
    deleted_dirs: list[str] = []

    project_dir_1 = tmp_path / "history-project"
    project_dir_2 = tmp_path / "History Project"
    project_dir_1.mkdir()
    project_dir_2.mkdir()

    monkeypatch.setattr(state, "_matching_project_dirs", lambda _project: [project_dir_1, project_dir_2])

    def _fake_rmtree(path: Path) -> None:
        deleted_dirs.append(str(path))

    monkeypatch.setattr(state.shutil, "rmtree", _fake_rmtree)
    monkeypatch.setattr(state, "delete_project_records", lambda project_id: removed_ids.append(project_id))

    removed_local_dirs, errors = state.delete_project("History Project")

    assert removed_local_dirs == 2
    assert errors == []
    assert removed_ids == ["history-project"]
    assert deleted_dirs == [str(project_dir_1), str(project_dir_2)]


def test_delete_project_collects_local_and_storage_errors(monkeypatch, tmp_path) -> None:
    doomed_dir = tmp_path / "doomed-project"
    doomed_dir.mkdir()

    monkeypatch.setattr(state, "_matching_project_dirs", lambda _project: [doomed_dir])

    def _raise_rmtree(_path: Path) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(state.shutil, "rmtree", _raise_rmtree)

    def _raise_records(_project_id: str) -> None:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(state, "delete_project_records", _raise_records)

    removed_local_dirs, errors = state.delete_project("Doomed Project")

    assert removed_local_dirs == 0
    assert len(errors) == 2
    assert "permission denied" in errors[0]
    assert "db unavailable" in errors[1]
