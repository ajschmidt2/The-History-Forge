from utils import split_script_into_scene_strings, split_script_into_scenes


def test_split_script_into_scenes_prefers_explicit_scene_break_delimiter() -> None:
    script = (
        "SCENE 01 | Arrival\nNARRATION: First section.\nVISUAL INTENT: Harbor.\nEND SCENE 01\n"
        "---SCENE_BREAK---\n"
        "SCENE 02 | Conflict\nNARRATION: Second section.\nVISUAL INTENT: Market.\nEND SCENE 02"
    )

    scenes = split_script_into_scenes(script, max_scenes=8, outline=None, wpm=160)

    # Rebalanced to requested count when content allows subdivision.
    assert len(scenes) == 8
    assert scenes[0].script_excerpt.startswith("SCENE 01")


def test_split_script_into_scenes_uses_scene_headings_without_delimiter() -> None:
    script = (
        "SCENE 01 | Opening\nNARRATION: Intro line. More context. More setup.\nVISUAL INTENT: Port city.\nEND SCENE 01\n\n"
        "SCENE 02 | Turning Point\nNARRATION: Middle line. Conflict rises. Stakes increase.\nVISUAL INTENT: Fortress.\nEND SCENE 02"
    )

    scenes = split_script_into_scenes(script, max_scenes=6, outline=None, wpm=150)

    assert len(scenes) == 6
    assert all(scene.script_excerpt for scene in scenes)


def test_split_script_into_scenes_falls_back_to_paragraphs_then_sentence_windows() -> None:
    paragraph_script = "Para one. More one.\n\nPara two. More two.\n\nPara three. More three."
    paragraph_scenes = split_script_into_scenes(paragraph_script, max_scenes=8, outline=None, wpm=150)
    assert len(paragraph_scenes) == 8

    sentence_script = "One. Two. Three. Four. Five. Six."
    sentence_scenes = split_script_into_scenes(sentence_script, max_scenes=2, outline=None, wpm=150)
    assert len(sentence_scenes) == 2
    assert sentence_scenes[0].script_excerpt.startswith("One")


def test_split_script_into_scenes_dedupes_repeated_chunks() -> None:
    script = "Same text. More words here.\n\nSame text. More words here.\n\nDifferent text with enough words to split later."
    scenes = split_script_into_scenes(script, max_scenes=4, outline=None, wpm=150)

    assert len(scenes) == 4
    normalized = {" ".join(scene.script_excerpt.lower().split()) for scene in scenes}
    assert len(normalized) >= 2


def test_split_scene_strings_is_deterministic() -> None:
    script = """
    First beat starts the story with context and setup.

    Second beat introduces conflict in the city center.

    Third beat explains consequences and recovery over time.
    """
    first = split_script_into_scene_strings(script, 5)
    second = split_script_into_scene_strings(script, 5)
    assert first == second
    assert len(first) == 5
