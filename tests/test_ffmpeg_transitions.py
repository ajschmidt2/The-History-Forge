import pytest

from src.video.ffmpeg_render import (
    _normalize_scene_duration,
    _normalize_xfade_transition,
    _safe_crossfade_duration,
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
