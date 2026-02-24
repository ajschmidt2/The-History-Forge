from utils import split_script_into_scene_strings, split_script_into_scenes


def test_split_script_into_scenes_prefers_explicit_scene_break_delimiter() -> None:
    script = (
        "SCENE 01 | Arrival\nNARRATION: First section.\nVISUAL INTENT: Harbor.\nEND SCENE 01\n"
        "---SCENE_BREAK---\n"
        "SCENE 02 | Conflict\nNARRATION: Second section.\nVISUAL INTENT: Market.\nEND SCENE 02"
    )

    scenes = split_script_into_scenes(script, max_scenes=8, outline=None, wpm=160)

    assert len(scenes) == 2
    assert scenes[0].script_excerpt.startswith("SCENE 01")
    assert scenes[1].script_excerpt.startswith("SCENE 02")


def test_split_script_into_scenes_uses_scene_headings_without_delimiter() -> None:
    script = (
        "SCENE 01 | Opening\nNARRATION: Intro line.\nVISUAL INTENT: Port city.\nEND SCENE 01\n\n"
        "SCENE 02 | Turning Point\nNARRATION: Middle line.\nVISUAL INTENT: Fortress.\nEND SCENE 02"
    )

    scenes = split_script_into_scenes(script, max_scenes=8, outline=None, wpm=150)

    assert len(scenes) == 2
    assert all(scene.script_excerpt.startswith("SCENE 0") for scene in scenes)


def test_split_script_into_scenes_supports_markdown_scene_headings() -> None:
    script = (
        "### Scene 1: Opening\nNARRATION: Intro line.\nVISUAL INTENT: Port city.\n\n"
        "### Scene 2: Turning Point\nNARRATION: Middle line.\nVISUAL INTENT: Fortress."
    )

    scenes = split_script_into_scenes(script, max_scenes=8, outline=None, wpm=150)

    assert len(scenes) == 2
    assert scenes[0].script_excerpt.startswith("### Scene 1")
    assert scenes[1].script_excerpt.startswith("### Scene 2")


def test_split_script_into_scenes_supports_end_scene_boundaries_without_delimiter() -> None:
    script = (
        "SCENE 01 | Opening\nNARRATION: Intro line.\nVISUAL INTENT: Port city.\nEND SCENE 01\n"
        "SCENE 02 | Turning Point\nNARRATION: Middle line.\nVISUAL INTENT: Fortress.\nEND SCENE 02"
    )

    scenes = split_script_into_scenes(script, max_scenes=8, outline=None, wpm=150)

    assert len(scenes) == 2
    assert scenes[0].script_excerpt.startswith("SCENE 01")
    assert scenes[1].script_excerpt.startswith("SCENE 02")


def test_split_script_into_scenes_falls_back_to_paragraphs_then_sentence_windows() -> None:
    paragraph_script = "Para one.\n\nPara two.\n\nPara three."
    paragraph_scenes = split_script_into_scenes(paragraph_script, max_scenes=8, outline=None, wpm=150)
    assert len(paragraph_scenes) == 3

    sentence_script = "One. Two. Three. Four. Five. Six."
    sentence_scenes = split_script_into_scenes(sentence_script, max_scenes=8, outline=None, wpm=150)
    assert len(sentence_scenes) == 2  # 3-sentence windows
    assert sentence_scenes[0].script_excerpt.startswith("One. Two. Three.")


def test_split_script_into_scenes_dedupes_repeated_chunks() -> None:
    script = "Same text.\n\nSame text.\n\nDifferent text."
    scenes = split_script_into_scenes(script, max_scenes=8, outline=None, wpm=150)

    assert len(scenes) == 2
    assert scenes[0].script_excerpt != scenes[1].script_excerpt


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
