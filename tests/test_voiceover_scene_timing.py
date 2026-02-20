from utils import Scene

from src.ui.tabs.voiceover import _fit_scene_durations_to_voiceover


def test_fit_scene_durations_to_voiceover_matches_target_total() -> None:
    scenes = [
        Scene(index=1, title='A', script_excerpt='one two three four five', visual_intent=''),
        Scene(index=2, title='B', script_excerpt='one two three four five six seven eight nine ten', visual_intent=''),
    ]

    durations = _fit_scene_durations_to_voiceover(scenes, 12.0, wpm=120)

    assert len(durations) == 2
    assert round(sum(durations), 2) == 12.0
    assert durations[1] > durations[0]


def test_fit_scene_durations_to_voiceover_handles_empty_excerpts() -> None:
    scenes = [
        Scene(index=1, title='A', script_excerpt='', visual_intent=''),
        Scene(index=2, title='B', script_excerpt='', visual_intent=''),
    ]

    durations = _fit_scene_durations_to_voiceover(scenes, 8.0, wpm=160)

    assert len(durations) == 2
    assert round(sum(durations), 2) == 8.0
