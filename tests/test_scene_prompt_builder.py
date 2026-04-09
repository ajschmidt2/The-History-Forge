from pathlib import Path
import json

from utils import Scene, generate_prompts_for_scenes
from src.video.ai_video_clips import _find_scene_prompts


def test_generate_prompts_builds_structured_scene_spec() -> None:
    scenes = [
        Scene(
            index=1,
            title="Aqueduct Inspection",
            script_excerpt="At dawn in Rome, an engineer lights an oil lamp and inspects the vaulted aqueduct interior for cracks.",
            visual_intent="engineer aqueduct dawn oil lamp",
        ),
        Scene(
            index=2,
            title="Archive Realization",
            script_excerpt="A weary official studies tax scrolls beside broken imperial seals while smoke rises beyond a cracked window.",
            visual_intent="official archive tax scrolls smoke",
        ),
    ]

    out = generate_prompts_for_scenes(scenes, tone="Documentary", style="Photorealistic cinematic")

    assert len(out) == 2
    for scene in out:
        assert scene.prompt_spec
        assert scene.video_prompt_spec
        assert scene.image_prompt
        assert scene.video_prompt
        assert scene.negative_prompt
        assert scene.scene_summary
        assert set(scene.prompt_scores) == {
            "script_alignment",
            "historical_specificity",
            "visual_clarity",
            "action_clarity_for_video",
        }
        assert "scene_id" in scene.prompt_spec
        assert "moment_selection" in scene.prompt_spec
        assert "continuity lock" in scene.video_prompt_spec
        assert "epic scene" not in scene.image_prompt.lower()


def test_find_scene_prompts_reads_video_and_negative_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    project_id = "prompt-parse"
    p = Path("data/projects") / project_id
    p.mkdir(parents=True, exist_ok=True)
    (p / "scenes.json").write_text(
        json.dumps(
            [
                {
                    "image_prompt": "A still image prompt",
                    "video_prompt": "A motion-focused video prompt",
                    "negative_prompt": "no text overlays",
                }
            ]
        ),
        encoding="utf-8",
    )

    prompts = _find_scene_prompts(project_id)
    assert prompts == [
        {
            "image_prompt": "A still image prompt",
            "video_prompt": "A motion-focused video prompt",
            "negative_prompt": "no text overlays",
        }
    ]
