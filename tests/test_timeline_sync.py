from types import SimpleNamespace

from src.ui.timeline_sync import (
    _apply_manual_scene_durations,
    _has_custom_transition,
    _media_files_from_session_scenes,
    _resolve_scene_video_path,
)
from src.video.timeline_schema import Meta, Scene, Timeline


def _timeline() -> Timeline:
    return Timeline(
        meta=Meta(project_id="p1", title="Demo", scene_duration=3.0),
        scenes=[
            Scene(id="scene_01", image_path="assets/images/s01.png", start=0.0, duration=3.0, caption=""),
            Scene(id="scene_02", image_path="assets/images/s02.png", start=3.0, duration=3.0, caption=""),
        ],
    )


def test_apply_manual_scene_durations_updates_duration_and_starts() -> None:
    timeline = _timeline()
    session_scenes = [
        SimpleNamespace(index=1, estimated_duration_sec=4.5),
        SimpleNamespace(index=2, estimated_duration_sec=2.0),
    ]

    _apply_manual_scene_durations(timeline, session_scenes)

    assert [round(scene.duration, 2) for scene in timeline.scenes] == [4.5, 2.0]
    assert [round(scene.start, 2) for scene in timeline.scenes] == [0.0, 4.5]
    assert round(float(timeline.meta.scene_duration or 0.0), 2) == 3.25


def test_apply_manual_scene_durations_ignores_invalid_values() -> None:
    timeline = _timeline()
    session_scenes = [
        SimpleNamespace(index=1, estimated_duration_sec=0),
        SimpleNamespace(index=2, estimated_duration_sec="bad"),
    ]

    _apply_manual_scene_durations(timeline, session_scenes)

    assert [round(scene.duration, 2) for scene in timeline.scenes] == [3.0, 3.0]
    assert [round(scene.start, 2) for scene in timeline.scenes] == [0.0, 3.0]


def test_apply_manual_scene_durations_can_lock_total_duration() -> None:
    timeline = _timeline()
    session_scenes = [
        SimpleNamespace(index=1, estimated_duration_sec=10.0),
        SimpleNamespace(index=2, estimated_duration_sec=2.0),
    ]

    _apply_manual_scene_durations(timeline, session_scenes, lock_total_duration_to_timeline=True)

    assert round(sum(scene.duration for scene in timeline.scenes), 2) == 6.0
    assert [round(scene.start, 2) for scene in timeline.scenes] == [0.0, 5.0]


def test_has_custom_transition_detects_non_fade_values() -> None:
    assert _has_custom_transition(["fade", "wipeleft"]) is True
    assert _has_custom_transition(["fade", "fade"]) is False
    assert _has_custom_transition([]) is False


def test_resolve_scene_video_path_supports_relative_assets_path(tmp_path) -> None:
    project_path = tmp_path / "project"
    video_path = project_path / "assets" / "videos" / "s01.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")

    resolved = _resolve_scene_video_path(project_path, "assets/videos/s01.mp4")

    assert resolved == video_path.resolve()


def test_media_files_from_session_scenes_prefers_video_clip_over_image(tmp_path) -> None:
    project_path = tmp_path / "project"
    images_dir = project_path / "assets" / "images"
    videos_dir = project_path / "assets" / "videos"
    images_dir.mkdir(parents=True, exist_ok=True)
    videos_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "s01.png").write_bytes(b"image")
    video = videos_dir / "s01.mp4"
    video.write_bytes(b"video")

    scene = SimpleNamespace(index=1, video_path="assets/videos/s01.mp4", video_url=None)
    media_files = _media_files_from_session_scenes(project_path, [scene])

    assert media_files == [video.resolve()]
