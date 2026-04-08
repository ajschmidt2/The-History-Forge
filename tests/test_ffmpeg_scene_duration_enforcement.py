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
