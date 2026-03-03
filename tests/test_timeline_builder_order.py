from pathlib import Path

from src.video.timeline_builder import build_default_timeline


def test_build_default_timeline_preserves_caller_order() -> None:
    # Callers are responsible for ordering; build_default_timeline must preserve it.
    # This ensures a video clip placed at scene 2 isn't sorted to the end.
    images = [Path("s01.png"), Path("aerial_clip.mp4"), Path("s03.png")]

    timeline = build_default_timeline(
        project_id="p1",
        title="t",
        images=images,
        voiceover_path=None,
        include_voiceover=False,
        include_music=False,
    )

    assert [Path(scene.image_path).name for scene in timeline.scenes] == [
        "s01.png",
        "aerial_clip.mp4",
        "s03.png",
    ]


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


def test_build_default_timeline_persists_transition_types() -> None:
    images = [Path("s01.png"), Path("s02.png"), Path("s03.png")]

    timeline = build_default_timeline(
        project_id="p1",
        title="t",
        images=images,
        voiceover_path=None,
        include_voiceover=False,
        include_music=False,
        transition_types=["fade", "wipeleft"],
    )

    assert timeline.meta.transition_types == ["fade", "wipeleft"]
