from pathlib import Path

from src.ui.tabs import scenes


def test_saved_video_choices_includes_local_and_cloud(monkeypatch) -> None:
    monkeypatch.setattr(scenes, "_saved_videos_for_project", lambda _project_id: [Path("data/projects/p/assets/videos/local.mp4")])
    monkeypatch.setattr(
        scenes,
        "_cloud_generated_videos_for_project",
        lambda _project_id: [{"filename": "remote.mp4", "url": "https://example.com/remote.mp4", "created_at": "2026-01-01"}],
    )

    choices = scenes._saved_video_choices("demo-project")

    assert [item["label"] for item in choices] == ["local.mp4 · local", "remote.mp4 · cloud"]
    assert choices[0]["video_path"] == "data/projects/p/assets/videos/local.mp4"
    assert choices[0]["video_url"] is None
    assert choices[1]["video_path"] is None
    assert choices[1]["video_url"] == "https://example.com/remote.mp4"


def test_saved_video_choices_drops_cloud_rows_without_url(monkeypatch) -> None:
    monkeypatch.setattr(scenes, "_saved_videos_for_project", lambda _project_id: [])
    monkeypatch.setattr(
        scenes,
        "_cloud_generated_videos_for_project",
        lambda _project_id: [{"filename": "missing.mp4", "url": ""}],
    )

    assert scenes._saved_video_choices("demo-project") == []
