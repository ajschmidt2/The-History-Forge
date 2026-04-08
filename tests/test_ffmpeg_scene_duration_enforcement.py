from __future__ import annotations

from pathlib import Path

import pytest

from src.video import ffmpeg_render


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


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
