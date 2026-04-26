from types import SimpleNamespace

import utils
from utils import Scene, generate_prompts_for_scenes, generate_short_script


def test_visual_controls_are_injected_into_scene_prompts() -> None:
    scenes = [
        Scene(
            index=1,
            title="Harbor Watch",
            script_excerpt="A guard watches ships gather beneath a heavy sky.",
            visual_intent="guard harbor ships storm light",
        )
    ]

    [scene] = generate_prompts_for_scenes(scenes, tone="Documentary", style="Photorealistic cinematic")

    assert "Visual style guidance:" in scene.image_prompt
    assert "Visual style guidance:" in scene.video_prompt
    assert scene.prompt_spec["global_visual_style_control"].startswith("# History Forge Global Visual Style")


def test_script_controls_are_injected_into_short_script_prompt(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_completion(client, **kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content="Final narration text.")
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])

    monkeypatch.setattr(utils, "_openai_client", lambda: object())
    monkeypatch.setattr(utils, "openai_chat_completion", _fake_completion)

    result = generate_short_script("A forgotten border fortress")

    assert result == "Final narration text."
    system_prompt = captured["messages"][0]["content"]
    user_prompt = captured["messages"][1]["content"]
    assert "History Forge Global Script Style" in system_prompt
    assert "History Forge Global Output Format" in system_prompt
    assert "Keep the final script between 140 and 155 spoken words." in user_prompt
    assert "The first line must create immediate intrigue, tension, surprise, or contradiction." in user_prompt
    assert "Avoid weak openings like 'Today we're looking at' or 'Let's talk about'." in user_prompt
