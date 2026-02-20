from types import SimpleNamespace

from src.ui.timeline_sync import _apply_manual_scene_durations
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
