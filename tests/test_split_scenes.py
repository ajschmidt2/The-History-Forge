from utils import split_script_into_scene_strings, split_script_into_scenes


def test_split_script_into_scenes_is_beat_aware_with_outline() -> None:
    script = (
        "The city was once protected by massive walls and a thriving trade economy. "
        "Rivals studied every weakness for decades. "
        "When new artillery arrived, the balance changed quickly. "
        "Defenders improvised but supply lines collapsed. "
        "After the breach, power shifted across the region for centuries."
    )
    outline = {
        "beats": [
            {"title": "Old Strength", "bullets": ["Walls", "Trade", "Stability"]},
            {"title": "Breaking Point", "bullets": ["Artillery", "Supply lines", "Breach"]},
            {"title": "Aftermath", "bullets": ["New rule", "Regional consequences"]},
        ]
    }

    scenes = split_script_into_scenes(script, max_scenes=8, outline=outline, wpm=160)

    assert len(scenes) == 8
    assert [scene.title for scene in scenes[:3]] == ["Old Strength", "Breaking Point", "Aftermath"]
    assert all(scene.title for scene in scenes)
    assert all(scene.estimated_duration_sec > 0 for scene in scenes)
    assert all(len([kw for kw in scene.visual_intent.split(",") if kw.strip()]) >= 5 for scene in scenes)


def test_split_script_into_scenes_without_outline_has_estimates_and_keywords() -> None:
    script = (
        "# Opening\n\nA merchant fleet crossed dangerous waters to connect distant empires.\n\n"
        "# Conflict\n\nPiracy, storms, and taxes made every voyage a gamble.\n\n"
        "# Legacy\n\nThose routes reshaped cities, languages, and diplomacy."
    )

    scenes = split_script_into_scenes(script, max_scenes=3, outline=None, wpm=150)

    assert len(scenes) == 3
    assert all(scene.title for scene in scenes)
    assert all(scene.script_excerpt for scene in scenes)
    assert all(scene.estimated_duration_sec > 0 for scene in scenes)
    assert all(len([kw for kw in scene.visual_intent.split(",") if kw.strip()]) >= 5 for scene in scenes)


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
