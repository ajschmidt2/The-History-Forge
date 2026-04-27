import json
from pathlib import Path

from src.video.ai_video_clips import (
    _build_prompt_variants,
    extract_video_url,
    generate_ai_video_clips,
    is_valid_video_file,
    write_video_artifact,
)


def test_extract_video_url_nested_dict_and_list() -> None:
    payload = {
        "result": {
            "outputs": [
                {"type": "thumbnail", "url": "https://example.com/preview.jpg"},
                {"video": {"url": "https://example.com/video.mp4?token=secret"}},
            ]
        }
    }
    assert extract_video_url(payload) == "https://example.com/video.mp4?token=secret"


def test_write_video_artifact_with_bytes(tmp_path: Path) -> None:
    output = tmp_path / "clip.mp4"
    ok, reason = write_video_artifact(b"0" * 4096, output)
    assert ok is True
    assert reason == ""
    assert is_valid_video_file(output)


def test_write_video_artifact_dict_without_video(tmp_path: Path) -> None:
    output = tmp_path / "clip.mp4"
    ok, reason = write_video_artifact({"status": "ok", "data": {}}, output)
    assert ok is False
    assert reason == "provider returned dict without video artifact"


def test_ai_video_clip_prompts_differentiate_from_neighboring_stills(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    project_id = "clip-neighbor-prompts"
    project_dir = Path("data/projects") / project_id
    images_dir = project_dir / "assets/images"
    images_dir.mkdir(parents=True, exist_ok=True)
    for index in range(1, 6):
        (images_dir / f"s{index:02d}.png").write_bytes(b"fake-image")

    (project_dir / "scenes.json").write_text(
        json.dumps(
            [
                {
                    "title": f"Scene {index}",
                    "scene_summary": f"Scene {index} still image",
                    "image_prompt": f"Still prompt {index}",
                    "video_prompt": f"Motion prompt {index}",
                }
                for index in range(1, 6)
            ]
        ),
        encoding="utf-8",
    )

    captured_prompts: list[str] = []

    def _fake_generate_scene_video(**kwargs):
        captured_prompts.append(kwargs["prompt"])
        Path(kwargs["output_path"]).write_bytes(b"0" * 2048)
        return {"ok": True}

    monkeypatch.setattr("src.video.ai_video_clips.generate_scene_video", _fake_generate_scene_video)
    monkeypatch.setattr("src.video.ai_video_clips._normalize_clip_orientation", lambda *args, **kwargs: None)

    clips = generate_ai_video_clips(project_id, tmp_path / "clips", provider="google_veo_lite")

    assert len([clip for clip in clips if clip is not None]) == 4
    assert len(captured_prompts) == 4
    assert all("Distinct clip direction" in prompt for prompt in captured_prompts)
    assert "avoid repeating the previous still scene (Scene 1 still image)" in captured_prompts[1]
    assert "avoid repeating the next still scene (Scene 3 still image)" in captured_prompts[1]


def test_build_prompt_variants_sanitizes_and_shortens() -> None:
    variants = _build_prompt_variants(
        base_prompt=(
            "Children run through flames during a violent tragedy with panic everywhere. "
            + "A" * 1200
        ),
        label="opening",
        packed={
            "title": "Scene 1",
            "scene_summary": "A tense family moment in a house at night",
            "prompt_spec": {
                "primary_subject": "children",
                "setting/location": "family home",
                "time_period": "1945",
                "visible_action": "running through flames",
                "emotional_tone": "panic",
            },
        },
        aspect_ratio="9:16",
    )

    assert variants
    assert all(len(prompt) <= 700 for prompt in variants)
    assert all("children" not in prompt.lower() for prompt in variants)
    assert all("violent" not in prompt.lower() for prompt in variants)
    assert all("flames" not in prompt.lower() for prompt in variants)
    assert all("never as visible writing" in prompt.lower() for prompt in variants)


def test_ai_video_clips_retry_with_safer_prompt_variant(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    project_id = "clip-retry-prompts"
    project_dir = Path("data/projects") / project_id
    images_dir = project_dir / "assets/images"
    images_dir.mkdir(parents=True, exist_ok=True)
    for index in range(1, 5):
        (images_dir / f"s{index:02d}.png").write_bytes(b"fake-image")

    (project_dir / "scenes.json").write_text(
        json.dumps(
            [
                {
                    "title": f"Scene {index}",
                    "scene_summary": f"Scene {index} still image with children and flames",
                    "image_prompt": f"Still prompt {index}",
                    "video_prompt": f"Children in flames prompt {index}",
                    "prompt_spec": {
                        "primary_subject": "children",
                        "setting/location": "family home",
                        "time_period": "1945",
                        "visible_action": "running through flames",
                    },
                }
                for index in range(1, 5)
            ]
        ),
        encoding="utf-8",
    )

    attempts: dict[str, int] = {}
    prompt_log: list[str] = []

    def _fake_generate_scene_video(**kwargs):
        output_path = Path(kwargs["output_path"])
        label = output_path.stem
        attempts[label] = attempts.get(label, 0) + 1
        prompt_log.append(kwargs["prompt"])
        if attempts[label] == 1:
            return {"ok": False, "error": "Gemini Veo returned no generated videos."}
        output_path.write_bytes(b"0" * 2048)
        return {"ok": True}

    monkeypatch.setattr("src.video.ai_video_clips.generate_scene_video", _fake_generate_scene_video)
    monkeypatch.setattr("src.video.ai_video_clips._normalize_clip_orientation", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.video.ai_video_clips.time.sleep", lambda *_args, **_kwargs: None)

    clips = generate_ai_video_clips(project_id, tmp_path / "clips", provider="google_veo_lite")

    assert len([clip for clip in clips if clip is not None]) == 4
    assert all(count >= 2 for count in attempts.values())
    assert any("safe, non-graphic" in prompt.lower() for prompt in prompt_log)
