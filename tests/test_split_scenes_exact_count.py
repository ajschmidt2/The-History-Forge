from utils import split_script_into_scene_strings


def _assert_exact(script: str, target: int) -> None:
    scenes, debug = split_script_into_scene_strings(script, target, return_debug=True)
    assert len(scenes) == target
    assert all(isinstance(s, str) for s in scenes)
    assert all(s.strip() for s in scenes)
    assert len(debug["word_counts"]) == target


def test_very_short_script_exact_counts() -> None:
    script = "A king rose. A king fell."
    for target in (1, 5, 12):
        _assert_exact(script, target)


def test_very_long_single_paragraph_exact_counts() -> None:
    sentence = "The empire expanded through reforms, war, trade, and difficult alliances."
    script = " ".join([sentence for _ in range(180)])
    for target in (1, 5, 12):
        _assert_exact(script, target)


def test_many_short_paragraphs_exact_counts() -> None:
    paragraphs = [f"Paragraph {i} explains a distinct historical beat in clear language." for i in range(1, 40)]
    script = "\n\n".join(paragraphs)
    for target in (1, 5, 12):
        _assert_exact(script, target)
