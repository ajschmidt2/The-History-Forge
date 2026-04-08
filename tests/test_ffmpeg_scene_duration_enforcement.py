from __future__ import annotations

from pathlib import Path

import pytest

from src.video import ffmpeg_render
from src.video.timeline_schema import CaptionStyle, Meta, Scene, Timeline


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def _make_timeline(num_scenes: int) -> Timeline:
    """Build a minimal Timeline with ``s01..sNN`` scenes whose image_path
    basenames match the scene id (e.g. ``s04.png``). This mirrors what the
    real image generator produces and lets tests exercise the filename-based
    AI-clip scene lookup without hitting ffmpeg or ffprobe.
    """
    scenes = [
        Scene(
            id=f"s{i:02d}",
            image_path=f"/tmp/project/assets/images/s{i:02d}.png",
            start=0.0,
            duration=3.0,
        )
        for i in range(1, num_scenes + 1)
    ]
    meta = Meta(
        project_id="test-project",
        title="test",
        caption_style=CaptionStyle(),
    )
    return Timeline(meta=meta, scenes=scenes)


_DUMMY_CLIP_PATHS = {
    "ai_opening_clip_path": "/clips/opening.mp4",
    "ai_q2_clip_path":      "/clips/q2.mp4",
    "ai_q3_clip_path":      "/clips/q3.mp4",
    "ai_q4_clip_path":      "/clips/q4.mp4",
}


def test_build_scene_final_clip_uses_copy_trim_for_ai_only(monkeypatch, tmp_path) -> None:
    ai_clip = _touch(tmp_path / "ai.mp4")
    still_clip = _touch(tmp_path / "still.mp4")
    output = tmp_path / "final.mp4"

    called: dict[str, bool] = {"copy": False}

    def _trim_copy(*args, **kwargs):
        called["copy"] = True
        _touch(output)
        return output

    monkeypatch.setattr(ffmpeg_render, "get_media_duration", lambda path: 4.0 if Path(path) == ai_clip else 0.0)
    monkeypatch.setattr(ffmpeg_render, "trim_clip_copy", _trim_copy)
    monkeypatch.setattr(ffmpeg_render, "ffprobe_duration", lambda _path: 3.0)

    built, strategy, _ai_duration = ffmpeg_render.build_scene_final_clip(
        scene_id="s01",
        still_scene_path=still_clip,
        ai_clip_path=ai_clip,
        target_duration=3.0,
        output_path=output,
        ffmpeg_commands=[],
        log_path=None,
        command_timeout_sec=None,
    )

    assert built == output
    assert strategy == "ai_only"
    assert called["copy"] is True


def test_compute_ai_scene_clip_mapping_matches_generator_for_default_14_scene_preset() -> None:
    """The render mapping must line up with the image indices used by
    ``src/video/ai_video_clips.py::generate_ai_video_clips`` so each of the
    four AI clips is attached to the scene it was generated for. Before this
    was fixed the mapping was hardcoded to s01/s03/s05/s07, which only matched
    8-scene projects; the default DailyShortPreset uses 14 scenes.
    """
    mapping = ffmpeg_render.compute_ai_scene_clip_mapping(14)
    assert mapping == {
        "s01": "ai_opening_clip_path",
        "s04": "ai_q2_clip_path",
        "s08": "ai_q3_clip_path",
        "s11": "ai_q4_clip_path",
    }


def test_compute_ai_scene_clip_mapping_matches_legacy_8_scene_layout() -> None:
    assert ffmpeg_render.compute_ai_scene_clip_mapping(8) == {
        "s01": "ai_opening_clip_path",
        "s03": "ai_q2_clip_path",
        "s05": "ai_q3_clip_path",
        "s07": "ai_q4_clip_path",
    }


def test_compute_ai_scene_clip_mapping_collapses_small_scene_counts_without_losing_opening() -> None:
    # With two scenes the q2/q3/q4 indices all collide with either scene 1 or
    # scene 2; the opening clip must always win so the first scene still
    # animates.
    mapping_two = ffmpeg_render.compute_ai_scene_clip_mapping(2)
    assert mapping_two["s01"] == "ai_opening_clip_path"
    # With one scene every clip falls onto s01 and only the opening survives.
    assert ffmpeg_render.compute_ai_scene_clip_mapping(1) == {
        "s01": "ai_opening_clip_path",
    }
    # Zero / negative inputs must be treated as a single scene, not crash.
    assert ffmpeg_render.compute_ai_scene_clip_mapping(0) == {
        "s01": "ai_opening_clip_path",
    }


def test_resolve_ai_clip_scene_mapping_survives_scene_count_shrink() -> None:
    """Clips generated on a 14-scene project must still attach to the
    surviving scenes when the user later renders with only 8 scenes.

    Source images recorded at generation time: s01.png, s04.png, s08.png,
    s11.png. After shrinking to 8 scenes, s11.png no longer exists so its
    clip must be reported as orphaned (added to ``render_warnings`` and
    returned in the orphan list) while the other three clips still attach
    to the scenes that currently hold their source filenames.
    """
    source_images_meta = {
        "ai_opening_clip_path": "s01.png",
        "ai_q2_clip_path":      "s04.png",
        "ai_q3_clip_path":      "s08.png",
        "ai_q4_clip_path":      "s11.png",
    }
    render_warnings: list[str] = []
    mapping, strategy, orphans = ffmpeg_render.resolve_ai_clip_scene_mapping(
        timeline=_make_timeline(8),
        raw_clip_paths=_DUMMY_CLIP_PATHS,
        source_images_meta=source_images_meta,
        render_warnings=render_warnings,
    )

    assert strategy == "filename_based"
    assert mapping == {
        "s01": "/clips/opening.mp4",
        "s04": "/clips/q2.mp4",
        "s08": "/clips/q3.mp4",
    }
    assert len(orphans) == 1
    assert "ai_q4_clip_path" in orphans[0]
    assert "s11.png" in orphans[0]
    assert render_warnings == orphans


def test_resolve_ai_clip_scene_mapping_survives_scene_count_grow() -> None:
    """Clips generated on an 8-scene project must still attach to the
    correct scenes when the user later renders with 14 scenes. All four
    source images still exist so nothing is orphaned, and the 10 new
    scenes get no AI clip.
    """
    source_images_meta = {
        "ai_opening_clip_path": "s01.png",
        "ai_q2_clip_path":      "s03.png",
        "ai_q3_clip_path":      "s05.png",
        "ai_q4_clip_path":      "s07.png",
    }
    mapping, strategy, orphans = ffmpeg_render.resolve_ai_clip_scene_mapping(
        timeline=_make_timeline(14),
        raw_clip_paths=_DUMMY_CLIP_PATHS,
        source_images_meta=source_images_meta,
    )

    assert strategy == "filename_based"
    assert mapping == {
        "s01": "/clips/opening.mp4",
        "s03": "/clips/q2.mp4",
        "s05": "/clips/q3.mp4",
        "s07": "/clips/q4.mp4",
    }
    assert orphans == []


def test_resolve_ai_clip_scene_mapping_falls_back_to_formula_for_legacy_payload() -> None:
    """Projects generated before ``ai_clip_source_images`` was persisted do
    not carry per-clip source metadata. The helper must fall back to the
    formula-based ``compute_ai_scene_clip_mapping`` path so existing projects
    keep rendering their AI clips.
    """
    mapping, strategy, orphans = ffmpeg_render.resolve_ai_clip_scene_mapping(
        timeline=_make_timeline(14),
        raw_clip_paths=_DUMMY_CLIP_PATHS,
        source_images_meta=None,
    )

    assert strategy == "formula_fallback"
    assert mapping == {
        "s01": "/clips/opening.mp4",
        "s04": "/clips/q2.mp4",
        "s08": "/clips/q3.mp4",
        "s11": "/clips/q4.mp4",
    }
    assert orphans == []

    # An empty dict (present but all values blank) must also trigger the
    # fallback, not produce an empty mapping.
    mapping_empty, strategy_empty, _ = ffmpeg_render.resolve_ai_clip_scene_mapping(
        timeline=_make_timeline(14),
        raw_clip_paths=_DUMMY_CLIP_PATHS,
        source_images_meta={
            "ai_opening_clip_path": "",
            "ai_q2_clip_path": "",
            "ai_q3_clip_path": "",
            "ai_q4_clip_path": "",
        },
    )
    assert strategy_empty == "formula_fallback"
    assert mapping_empty == mapping


def test_build_scene_final_clip_raises_when_duration_mismatch(monkeypatch, tmp_path) -> None:
    still_clip = _touch(tmp_path / "still.mp4")
    output = tmp_path / "final.mp4"

    monkeypatch.setattr(ffmpeg_render, "get_media_duration", lambda _path: 0.0)
    monkeypatch.setattr(ffmpeg_render, "trim_clip", lambda *args, **kwargs: _touch(output))
    monkeypatch.setattr(ffmpeg_render, "ffprobe_duration", lambda _path: 2.7)

    with pytest.raises(RuntimeError, match="duration mismatch"):
        ffmpeg_render.build_scene_final_clip(
            scene_id="s01",
            still_scene_path=still_clip,
            ai_clip_path=None,
            target_duration=3.0,
            output_path=output,
            ffmpeg_commands=[],
            log_path=None,
            command_timeout_sec=None,
        )
