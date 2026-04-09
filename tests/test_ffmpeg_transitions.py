import pytest

from src.video.ffmpeg_render import (
    _normalize_scene_duration,
    _normalize_xfade_transition,
    _safe_crossfade_duration,
    build_stitch_plan_from_final_clips,
    validate_stitch_plan,
)


def test_normalize_xfade_transition_accepts_known_values() -> None:
    assert _normalize_xfade_transition("wipeleft") == "wipeleft"


def test_normalize_xfade_transition_falls_back_to_fade() -> None:
    assert _normalize_xfade_transition("unknown") == "fade"


def test_safe_crossfade_duration_clamps_to_shortest_scene() -> None:
    assert _safe_crossfade_duration([2.0, 0.4], requested=0.5, fps=30) == pytest.approx(0.3666666667)


def test_safe_crossfade_duration_disables_invalid_requests() -> None:
    assert _safe_crossfade_duration([1.0, 1.0], requested=-0.2, fps=30) == 0.0


def test_normalize_scene_duration_enforces_minimum_frame_duration() -> None:
    assert _normalize_scene_duration(0.001, fps=25, scene_id="a") == pytest.approx(0.04)


def test_normalize_scene_duration_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        _normalize_scene_duration(float("inf"), fps=30, scene_id="a")


def test_build_stitch_plan_uses_cumulative_offset_formula(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    clips = [tmp_path / "s01.mp4", tmp_path / "s02.mp4", tmp_path / "s03.mp4"]
    duration_map = {
        str(clips[0]): 6.582545,
        str(clips[1]): 5.0,
        str(clips[2]): 4.2,
    }
    monkeypatch.setattr(
        "src.video.ffmpeg_render.ffprobe_duration",
        lambda path: duration_map[str(path)],
    )

    plan = build_stitch_plan_from_final_clips(clips, transition_duration=1.0)

    assert plan["computed_offsets"] == pytest.approx([5.582545, 9.582545])
    assert plan["expected_stitched_duration"] == pytest.approx(13.782545)


def test_validate_stitch_plan_rejects_non_increasing_offsets() -> None:
    plan = {
        "actual_durations": [5.0, 5.0, 5.0],
        "transition_duration": 1.0,
        "computed_offsets": [4.0, 3.9],
        "expected_stitched_duration": 13.0,
    }
    with pytest.raises(RuntimeError):
        validate_stitch_plan(plan)
