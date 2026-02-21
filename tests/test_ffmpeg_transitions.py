from src.video.ffmpeg_render import _normalize_xfade_transition


def test_normalize_xfade_transition_accepts_known_values() -> None:
    assert _normalize_xfade_transition("wipeleft") == "wipeleft"


def test_normalize_xfade_transition_falls_back_to_fade() -> None:
    assert _normalize_xfade_transition("unknown") == "fade"
