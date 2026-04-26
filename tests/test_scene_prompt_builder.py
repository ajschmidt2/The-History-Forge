from io import BytesIO
from pathlib import Path
import json

from PIL import Image, ImageDraw

from utils import (
    Scene,
    _build_safe_fallback_image_prompt,
    generate_image_for_scene,
    generate_prompts_for_scenes,
    inspect_generated_image_artifacts,
)
from src.video.ai_video_clips import _find_scene_prompts, generate_ai_video_clips


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
        assert "Scene uniqueness:" in scene.image_prompt
        assert "Scene uniqueness:" in scene.video_prompt
        assert "Script anchor keywords:" not in scene.image_prompt
        assert "Script anchor keywords:" not in scene.video_prompt
        assert "Absolutely no readable text" in scene.image_prompt
        assert "Absolutely no readable text" in scene.video_prompt


def test_generate_prompts_uses_global_location_without_repeating_character_appearance() -> None:
    scene = Scene(
        index=1,
        title="Harbor Barrier",
        script_excerpt="A heavy chain blocks the harbor mouth as ships hesitate offshore.",
        visual_intent="chain harbor ships blockade",
    )
    visual_context = {
        "time_period": "Byzantine Empire, 8th century",
        "location": "Constantinople, the Golden Horn",
        "clothing_style": "Byzantine military tunics and robes in rich colors, adorned with gold embroidery",
        "visual_atmosphere": "tense and dramatic with overcast skies",
        "character_name": "Emperor Leo III",
        "character_appearance": "middle-aged, with dark hair and a beard, wearing a laurel crown and a regal cloak",
        "visual_style": "cinematic historical realism",
        "color_palette": "deep blues and metallic grays",
    }

    [out] = generate_prompts_for_scenes([scene], tone="Documentary", style="Photorealistic cinematic", visual_context=visual_context)

    assert "Constantinople, the Golden Horn" in out.image_prompt
    assert "appearance:" not in out.image_prompt


def test_generate_prompts_prefers_named_subjects_and_concrete_actions() -> None:
    scene = Scene(
        index=1,
        title="Alpine Crossing",
        script_excerpt="Hannibal Barca leads war elephants through a snowstorm as his army struggles over a narrow Alpine pass.",
        visual_intent="hannibal elephants alpine pass snowstorm",
    )

    [out] = generate_prompts_for_scenes([scene], tone="Documentary", style="Photorealistic cinematic")

    spec = out.prompt_spec
    assert spec["primary_subject"] == "Hannibal Barca"
    assert "path" not in spec["primary_subject"].lower()
    assert "stakes" not in spec["primary_subject"].lower()
    assert "snowstorm" in spec["visible_action"].lower()
    assert "war elephants" in spec["visible_action"].lower()
    assert "Hannibal Barca" in out.image_prompt


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
            "title": "",
            "script_excerpt": "",
            "scene_summary": "",
            "image_prompt": "A still image prompt",
            "video_prompt": "A motion-focused video prompt",
            "negative_prompt": "no text overlays",
        }
    ]


def test_ai_video_clip_prompts_are_compact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    project_id = "clip-compact"
    project_dir = Path("data/projects") / project_id
    (project_dir / "assets/images").mkdir(parents=True, exist_ok=True)
    (project_dir / "assets/images" / "s01.png").write_bytes(b"fake-image")
    (project_dir / "scenes.json").write_text(
        json.dumps(
            [
                {
                    "image_prompt": "Still frame. Visual style guidance: " + ("very long text " * 400),
                    "video_prompt": "Motion frame. Visual style guidance: " + ("very long text " * 400),
                    "negative_prompt": "no text overlays",
                    "visual_context": {"location": "Ancient harbor"},
                    "prompt_spec": {
                        "anchor_keywords": ["harbor", "ships", "fog"],
                        "scene_uniqueness_note": "Use a different angle than adjacent scenes.",
                        "global_visual_style_control": "# History Forge Global Visual Style\n- cinematic\n- photorealistic\n- historically grounded\n- dramatic lighting\n- realistic textures\n",
                    },
                    "video_spec": {
                        "opening frame description": "Opening frame: harbor at dawn",
                        "subject motion": "Subject motion: ships drift through fog",
                        "ending frame description": "Ending frame: harbor opens to distant fleet",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    captured: list[str] = []

    def _fake_generate_scene_video(**kwargs):
        captured.append(kwargs["prompt"])
        Path(kwargs["output_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["output_path"]).write_bytes(b"fake-video")
        return {"ok": True, "error": ""}

    monkeypatch.setattr("src.video.ai_video_clips.generate_scene_video", _fake_generate_scene_video)
    monkeypatch.setattr("src.video.ai_video_clips._normalize_clip_orientation", lambda *args, **kwargs: None)

    generate_ai_video_clips(project_id=project_id, tmp_dir=tmp_path / "tmp")

    assert captured
    assert all(len(prompt) <= 2600 for prompt in captured)
    assert all("History Forge Global Visual Style" not in prompt for prompt in captured)


def test_safe_fallback_image_prompt_softens_sensitive_language() -> None:
    scene = Scene(
        index=1,
        title="Persecution",
        script_excerpt="The persecution of entire communities fueled hatred and violence.",
        visual_intent="persecution hatred violence",
    )
    scene.prompt_spec = {
        "primary_subject": "the Plague Doctor",
        "setting/location": "medieval town street",
        "time_period": "Europe, 14th century",
        "camera_framing": "close detail shot, 50mm lens equivalent",
        "composition_notes": "tight focus on central figure",
        "lighting": "candlelight and fog",
        "wardrobe_or_architecture_details": "beak-like mask and dark cloak",
        "secondary_subjects": ["communities", "violence"],
    }

    prompt = _build_safe_fallback_image_prompt(scene, aspect_ratio="9:16", visual_style="Documentary realism")

    assert "non-graphic historical scene" in prompt
    assert "persecution" not in prompt.lower()
    assert "violence" not in prompt.lower()


def test_generate_image_prompt_does_not_embed_scene_metadata_lines() -> None:
    scene = Scene(
        index=1,
        title="Rise of Dandara",
        script_excerpt="Ultimately she became overshadowed by later myths.",
        visual_intent="dandara myth overshadowed",
    )

    [out] = generate_prompts_for_scenes([scene], tone="Documentary", style="Photorealistic cinematic")

    assert "Scene title:" not in out.image_prompt
    assert "Script anchor excerpt:" not in out.image_prompt
    assert "Narration context:" not in out.image_prompt
    assert "Visual intent:" not in out.image_prompt


def _png_bytes_from_image(img: Image.Image) -> bytes:
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def test_inspect_generated_image_artifacts_flags_white_bands() -> None:
    img = Image.new("RGB", (720, 1280), (118, 92, 60))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 719, 59), fill=(255, 255, 255))
    draw.rectangle((0, 1220, 719, 1279), fill=(255, 255, 255))

    findings = inspect_generated_image_artifacts(img)

    assert any("white band" in finding for finding in findings)


def test_inspect_generated_image_artifacts_flags_text_like_overlay() -> None:
    img = Image.new("RGB", (720, 1280), (84, 68, 46))
    draw = ImageDraw.Draw(img)
    draw.text((180, 320), "overshadowed", fill=(248, 248, 248))
    draw.text((260, 520), "Ultimately", fill=(248, 248, 248))

    findings = inspect_generated_image_artifacts(img)

    assert any("text overlay" in finding for finding in findings)


def test_generate_image_for_scene_retries_after_artifact_rejection(monkeypatch) -> None:
    first = Image.new("RGB", (720, 1280), (88, 72, 52))
    first_draw = ImageDraw.Draw(first)
    first_draw.text((180, 320), "overshadowed", fill=(248, 248, 248))
    first_draw.text((260, 520), "Ultimately", fill=(248, 248, 248))

    second = Image.new("RGB", (720, 1280), (88, 72, 52))
    second_draw = ImageDraw.Draw(second)
    second_draw.ellipse((220, 280, 500, 820), fill=(132, 112, 84))

    responses = [_png_bytes_from_image(first), _png_bytes_from_image(second)]
    seen_prompts: list[str] = []

    def _fake_generate_scene_image_bytes(prompt: str, **kwargs):
        seen_prompts.append(prompt)
        return [responses.pop(0)]

    monkeypatch.setattr("utils.generate_scene_image_bytes", _fake_generate_scene_image_bytes)

    scene = Scene(
        index=1,
        title="Test Scene",
        script_excerpt="A tense historical moment unfolds in torchlight.",
        visual_intent="torchlight historical tension",
        image_prompt="Cinematic historical tableau with no text in frame.",
    )

    updated = generate_image_for_scene(scene)

    assert updated.image_bytes
    assert len(seen_prompts) == 2
    assert "Full-bleed edge-to-edge image only" in seen_prompts[1]
