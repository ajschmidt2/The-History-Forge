from pathlib import Path

from src.video.timeline_builder import build_default_timeline


def test_build_default_timeline_sorts_scene_media_by_scene_number() -> None:
    images = [Path("s10.png"), Path("s2.png"), Path("s01.png")]

    timeline = build_default_timeline(
        project_id="p1",
        title="t",
        images=images,
        voiceover_path=None,
        include_voiceover=False,
        include_music=False,
    )

    assert [Path(scene.image_path).name for scene in timeline.scenes] == ["s01.png", "s2.png", "s10.png"]


def test_build_default_timeline_disable_motion_sets_none() -> None:
    images = [Path("s01.png"), Path("s02.png")]

    timeline = build_default_timeline(
        project_id="p1",
        title="t",
        images=images,
        voiceover_path=None,
        include_voiceover=False,
        include_music=False,
        enable_motion=False,
    )

    assert all(scene.motion is None for scene in timeline.scenes)
