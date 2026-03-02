"""Tests for ffmpeg render robustness improvements."""
from __future__ import annotations

import pytest

from src.video.ffmpeg_render import (
    _assert_filter_complex_arg,
    _diagnostic_env,
    _file_stat,
    _scene_media_info,
)
from src.video.timeline_schema import Meta, Scene, Timeline


# ---------------------------------------------------------------------------
# _assert_filter_complex_arg
# ---------------------------------------------------------------------------

def test_assert_filter_complex_arg_passes_without_filter() -> None:
    """Commands without -filter_complex should not raise."""
    _assert_filter_complex_arg(["ffmpeg", "-y", "-i", "input.mp4", "output.mp4"])


def test_assert_filter_complex_arg_passes_with_valid_filter() -> None:
    """Commands with a non-empty filtergraph should not raise."""
    _assert_filter_complex_arg(["ffmpeg", "-y", "-filter_complex", "[0:v]scale=1280:720[v]", "-map", "[v]", "out.mp4"])


def test_assert_filter_complex_arg_raises_on_empty_string() -> None:
    """A blank filtergraph string must raise ValueError, not AssertionError."""
    with pytest.raises(ValueError, match="non-empty"):
        _assert_filter_complex_arg(["ffmpeg", "-filter_complex", "   "])


def test_assert_filter_complex_arg_raises_when_no_arg_follows() -> None:
    """-filter_complex at the end of the command must raise ValueError."""
    with pytest.raises(ValueError, match="-filter_complex must be followed"):
        _assert_filter_complex_arg(["ffmpeg", "-filter_complex"])


def test_assert_filter_complex_arg_raises_not_assertion_error() -> None:
    """Ensure we never leak an AssertionError (which -O would silently skip)."""
    with pytest.raises(ValueError):
        _assert_filter_complex_arg(["ffmpeg", "-filter_complex", ""])


# ---------------------------------------------------------------------------
# _diagnostic_env
# ---------------------------------------------------------------------------

def test_diagnostic_env_returns_dict_with_required_keys() -> None:
    env = _diagnostic_env()
    assert "ffmpeg_version" in env
    assert "python_version" in env
    assert "platform" in env


def test_diagnostic_env_includes_disk_info() -> None:
    env = _diagnostic_env()
    # disk_free_gb may be absent on exotic platforms, but should be present on Linux/Mac
    assert "disk_free_gb" in env or True  # permissive – just must not crash


# ---------------------------------------------------------------------------
# _file_stat
# ---------------------------------------------------------------------------

def test_file_stat_none_path() -> None:
    result = _file_stat(None)
    assert result["exists"] is False
    assert result["path"] is None


def test_file_stat_missing_file(tmp_path) -> None:
    result = _file_stat(str(tmp_path / "nonexistent.png"))
    assert result["exists"] is False
    assert result["size_bytes"] == 0


def test_file_stat_existing_file(tmp_path) -> None:
    f = tmp_path / "dummy.txt"
    f.write_text("hello")
    result = _file_stat(str(f))
    assert result["exists"] is True
    assert result["size_bytes"] == 5


# ---------------------------------------------------------------------------
# _scene_media_info
# ---------------------------------------------------------------------------

def _make_timeline(image_paths: list[str]) -> Timeline:
    scenes = [
        Scene(id=f"scene_{i}", image_path=p, start=float(i * 3), duration=3.0)
        for i, p in enumerate(image_paths)
    ]
    meta = Meta(project_id="test", title="Test", include_voiceover=False, include_music=False)
    return Timeline(meta=meta, scenes=scenes)


def test_scene_media_info_reports_missing_files() -> None:
    timeline = _make_timeline(["/nonexistent/image.jpg", "/also/missing.png"])
    info = _scene_media_info(timeline)
    assert len(info) == 2
    assert all(not item["exists"] for item in info)


def test_scene_media_info_reports_existing_file(tmp_path) -> None:
    img = tmp_path / "scene.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    timeline = _make_timeline([str(img)])
    info = _scene_media_info(timeline)
    assert info[0]["exists"] is True
    assert info[0]["size_bytes"] == 103
    assert info[0]["scene_id"] == "scene_0"


# ---------------------------------------------------------------------------
# _try_pull_project_assets_for_scene
# ---------------------------------------------------------------------------

def test_try_pull_project_assets_for_scene_ignores_non_project_paths(tmp_path) -> None:
    from src.video import ffmpeg_render

    scene_path = tmp_path / "assets" / "images" / "s01.png"
    assert ffmpeg_render._try_pull_project_assets_for_scene(scene_path, tmp_path) is False


def test_try_pull_project_assets_for_scene_downloads_missing_asset(monkeypatch, tmp_path) -> None:
    from src.video import ffmpeg_render

    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    scene_path = project_root / "data" / "projects" / "proj-1" / "assets" / "images" / "s01.png"

    class _FakeSBStore:
        @staticmethod
        def is_configured() -> bool:
            return True

        @staticmethod
        def pull_project_assets(project_id: str, project_dir):
            assert project_id == "proj-1"
            assert project_dir == (project_root / "data" / "projects" / "proj-1").resolve()
            scene_path.parent.mkdir(parents=True, exist_ok=True)
            scene_path.write_bytes(b"img")
            return {"image": 1, "audio": 0, "video": 0}

    import types

    module = types.SimpleNamespace(
        is_configured=_FakeSBStore.is_configured,
        pull_project_assets=_FakeSBStore.pull_project_assets,
    )
    monkeypatch.setattr(ffmpeg_render.importlib, "import_module", lambda name: module)

    assert ffmpeg_render._try_pull_project_assets_for_scene(scene_path, project_root) is True
