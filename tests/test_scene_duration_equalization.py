from src.ui.tabs.scenes import _equal_scene_durations


def test_equal_scene_durations_matches_total() -> None:
    durations = _equal_scene_durations(scene_count=5, total_duration=23.0)

    assert len(durations) == 5
    assert round(sum(durations), 6) == 23.0
    assert all(abs(d - durations[0]) < 1e-9 for d in durations[:-1])


def test_equal_scene_durations_handles_invalid_inputs() -> None:
    assert _equal_scene_durations(scene_count=0, total_duration=10.0) == []
    assert _equal_scene_durations(scene_count=4, total_duration=0.0) == []
