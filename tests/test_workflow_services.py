import json
from pathlib import Path

from src.workflow.models import StepStatus
from src.workflow.project_io import load_project_payload, load_scenes, save_project_payload
from src.workflow.services import (
    FullWorkflowOptions,
    PipelineOptions,
    StepResult,
    estimate_youtube_short_scene_count,
    run_full_workflow,
    run_generate_script,
    run_generate_short_script,
    run_generate_voiceover,
    run_render_video,
    run_split_scenes,
    run_sync_timeline,
)


def test_estimate_youtube_short_scene_count_scales_around_default() -> None:
    assert estimate_youtube_short_scene_count("word " * 150) == 14
    assert estimate_youtube_short_scene_count("word " * 80) < 14
    assert estimate_youtube_short_scene_count("word " * 220) > 14


def test_run_split_scenes_persists_scene_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "svc-scenes"
    save_project_payload(
        project_id,
        {
            "project_id": project_id,
            "script_text": "One short paragraph about history. Another sentence for timing.",
            "max_scenes": 3,
        },
    )

    result = run_split_scenes(project_id, PipelineOptions(number_of_scenes=3))
    assert result.status == StepStatus.COMPLETED
    assert result.outputs["scene_count"] == 3

    scenes_file = Path("data/projects") / project_id / "scenes.json"
    assert scenes_file.exists()
    scenes = json.loads(scenes_file.read_text(encoding="utf-8"))
    assert isinstance(scenes, list)
    assert len(scenes) == 3


def test_run_split_scenes_auto_sizes_default_youtube_short_count(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "svc-short-scenes"
    save_project_payload(
        project_id,
        {
            "project_id": project_id,
            "script_text": " ".join(f"word{i}" for i in range(150)),
            "script_profile": "youtube_short_60s",
            "automation_mode": "topic_to_short_video",
            "max_scenes": 8,
        },
    )

    result = run_split_scenes(
        project_id,
        PipelineOptions(
            number_of_scenes=14,
            script_profile="youtube_short_60s",
            automation_mode="topic_to_short_video",
        ),
    )
    assert result.status == StepStatus.COMPLETED
    assert result.outputs["scene_count"] == 14
    assert len(load_scenes(project_id)) == 14

    payload = load_project_payload(project_id)
    assert payload["resolved_scene_count"] == 14
    assert payload["script_word_count"] == 150


def test_run_split_scenes_auto_sizes_longer_short_script(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "svc-long-short-scenes"
    save_project_payload(
        project_id,
        {
            "project_id": project_id,
            "script_text": " ".join(f"word{i}" for i in range(220)),
            "script_profile": "youtube_short_60s",
            "automation_mode": "topic_to_short_video",
        },
    )

    result = run_split_scenes(project_id, PipelineOptions(number_of_scenes=14))
    assert result.status == StepStatus.COMPLETED
    assert result.outputs["scene_count"] > 14


def test_run_sync_timeline_fills_missing_durations(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "svc-timeline"
    save_project_payload(
        project_id,
        {
            "project_id": project_id,
            "project_title": "Svc Timeline",
            "scene_wpm": 160,
            "scene_transition_types": [],
        },
    )
    scenes = [
        {
            "index": 1,
            "title": "S1",
            "script_excerpt": "A brief narration line.",
            "visual_intent": "v1",
            "image_prompt": "",
            "estimated_duration_sec": 0,
        },
        {
            "index": 2,
            "title": "S2",
            "script_excerpt": "Another brief narration line for timing.",
            "visual_intent": "v2",
            "image_prompt": "",
            "estimated_duration_sec": 0,
        },
    ]
    (Path("data/projects") / project_id).mkdir(parents=True, exist_ok=True)
    (Path("data/projects") / project_id / "scenes.json").write_text(json.dumps(scenes), encoding="utf-8")

    def _fake_prompts(pid, options):
        loaded = load_scenes(pid)
        for scene in loaded:
            scene.image_prompt = f"Prompt {scene.index}"
        from src.workflow.project_io import save_scenes

        save_scenes(pid, loaded)
        from src.workflow.services import StepResult

        return StepResult(project_id=pid, step="prompts", status=StepStatus.COMPLETED)

    def _fake_sync(**kwargs):
        timeline_path = kwargs["project_path"] / "timeline.json"
        timeline_path.write_text("{}", encoding="utf-8")
        return timeline_path

    monkeypatch.setattr("src.workflow.services.run_generate_prompts", _fake_prompts)
    monkeypatch.setattr("src.workflow.services.sync_timeline_for_project", _fake_sync)

    result = run_sync_timeline(project_id, PipelineOptions())
    assert result.status == StepStatus.COMPLETED

    refreshed = load_scenes(project_id)
    assert all(float(scene.estimated_duration_sec) > 0 for scene in refreshed)


def test_run_generate_script_uses_existing_script_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "svc-existing-script"
    save_project_payload(
        project_id,
        {
            "project_id": project_id,
            "script_text": "Preloaded script text",
            "topic": "",
        },
    )

    result = run_generate_script(project_id, PipelineOptions())
    assert result.status == StepStatus.SKIPPED
    assert "Existing script text" in result.message

    script_path = Path("data/projects") / project_id / "script.txt"
    assert script_path.exists()
    assert script_path.read_text(encoding="utf-8") == "Preloaded script text"


def test_run_generate_voiceover_skips_when_silent_fallback_enabled_without_voice_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "svc-voiceover-silent"
    save_project_payload(
        project_id,
        {
            "project_id": project_id,
            "script_text": "Narration to synthesize.",
        },
    )

    monkeypatch.setattr("src.workflow.services._resolve_voice_id", lambda *args, **kwargs: "")
    result = run_generate_voiceover(project_id, PipelineOptions(allow_silent_render=True, include_voiceover=True))
    assert result.status == StepStatus.SKIPPED
    assert "silent render" in result.message


def test_run_full_workflow_skips_existing_steps_for_resume(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        "src.workflow.services._step_outputs_exist",
        lambda project_id, step: step in {"scenes", "narrative", "prompts", "images", "effects"},
    )
    monkeypatch.setattr(
        "src.workflow.services.run_generate_voiceover",
        lambda project_id, options=None: StepResult(project_id, "voiceover", StepStatus.SKIPPED, message="no voice"),
    )
    monkeypatch.setattr(
        "src.workflow.services.run_render_video",
        lambda project_id, options=None: StepResult(project_id, "render", StepStatus.COMPLETED, outputs={"video_path": "renders/final.mp4"}),
    )

    from src.workflow.project_io import save_project_payload

    project_id = "svc-full-resume"
    save_project_payload(project_id, {"project_id": project_id, "script_text": "Existing"})
    result = run_full_workflow(
        project_id,
        FullWorkflowOptions(mode="resume_missing", pipeline=PipelineOptions(allow_silent_render=True, include_voiceover=True, automation_mode="existing_script_full_workflow")),
    )
    assert result.failed_step == ""
    assert "scenes" in result.skipped_steps
    assert "render" in result.completed_steps


def test_run_generate_voiceover_with_openai_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "svc-voiceover-openai"
    save_project_payload(
        project_id,
        {
            "project_id": project_id,
            "script_text": "Narration to synthesize.",
            "tts_provider": "openai",
            "openai_tts_model": "gpt-4o-mini-tts",
            "openai_tts_voice": "alloy",
        },
    )

    monkeypatch.setattr("src.workflow.services.generate_voiceover_with_provider", lambda text, settings, output_path=None: (b"fake-mp3", None))

    result = run_generate_voiceover(project_id, PipelineOptions(tts_provider="openai", openai_tts_model="gpt-4o-mini-tts", openai_tts_voice="alloy"))
    assert result.status == StepStatus.COMPLETED
    assert result.outputs.get("provider") == "openai"


def test_run_generate_short_script_persists_script(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "svc-short-script"
    save_project_payload(project_id, {"project_id": project_id, "topic": "Battle of Cannae"})

    monkeypatch.setattr("src.workflow.services.generate_short_script", lambda **kwargs: "Hook line. Middle progression. Closing line.")

    result = run_generate_short_script(project_id, PipelineOptions(topic="Battle of Cannae", automation_mode="topic_to_short_video"))
    assert result.status == StepStatus.COMPLETED

    script_path = Path("data/projects") / project_id / "script.txt"
    assert script_path.exists()
    assert "Hook line" in script_path.read_text(encoding="utf-8")


def test_run_full_workflow_topic_mode_requires_topic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "svc-topic-required"
    save_project_payload(project_id, {"project_id": project_id})

    result = run_full_workflow(project_id, FullWorkflowOptions(pipeline=PipelineOptions(automation_mode="topic_to_short_video", topic="")))
    assert result.failed_step == "script"
    assert any("Topic is required" in msg for msg in result.warnings)


def test_load_options_hardens_automation_payload_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "svc-options-harden"
    save_project_payload(
        project_id,
        {
            "project_id": project_id,
            "scene_count": 999,
            "max_scenes": 999,
            "aspect_ratio": "1:1",
            "enable_subtitles": "false",
            "enable_music": "true",
            "automation_generate_voiceover": "false",
            "music_volume_relative_to_voiceover": "2.5",
            "variations_per_scene": 0,
            "video_effects_style": "Ken Burns - Dramatic",
        },
    )

    from src.workflow.services import _load_options

    _, options = _load_options(project_id, None)

    assert options.number_of_scenes == 75
    assert options.aspect_ratio == "16:9"
    assert options.include_subtitles is False
    assert options.include_music is True
    assert options.include_voiceover is False


def test_run_render_video_auto_rebuilds_invalid_timeline_references(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "svc-render-rebuild"
    pdir = Path("data/projects") / project_id
    (pdir / "assets/images").mkdir(parents=True, exist_ok=True)
    (pdir / "assets/music").mkdir(parents=True, exist_ok=True)
    (pdir / "assets/images/s01.png").write_bytes(b"png")
    (pdir / "assets/music/bed.mp3").write_bytes(b"mp3")

    save_project_payload(
        project_id,
        {
            "project_id": project_id,
            "project_title": "Render Rebuild",
            "script_text": "Line one.",
            "scene_wpm": 160,
            "aspect_ratio": "9:16",
            "enable_subtitles": False,
            "enable_music": True,
            "selected_music_track": str(pdir / "assets/music/bed.mp3"),
        },
    )
    (pdir / "scenes.json").write_text(
        json.dumps(
            [
                {
                    "index": 1,
                    "title": "S1",
                    "script_excerpt": "A line.",
                    "visual_intent": "v1",
                    "image_prompt": "prompt",
                    "estimated_duration_sec": 2,
                }
            ]
        ),
        encoding="utf-8",
    )

    call_count = {"sync": 0}

    def _fake_sync_timeline(project_id_arg, options=None):
        call_count["sync"] += 1
        timeline_path = Path("data/projects") / project_id_arg / "timeline.json"
        if call_count["sync"] == 1:
            timeline_path.write_text(
                json.dumps(
                    {
                        "meta": {
                            "project_id": project_id_arg,
                            "title": "bad",
                            "aspect_ratio": "16:9",
                            "resolution": "1280x720",
                            "burn_captions": True,
                            "include_voiceover": False,
                            "include_music": False,
                            "enable_motion": True,
                            "video_effects_style": "Ken Burns - Standard",
                        },
                        "scenes": [
                            {"id": "s01", "image_path": "/does/not/exist.png", "start": 0, "duration": 2}
                        ],
                    }
                ),
                encoding="utf-8",
            )
        else:
            timeline_path.write_text(
                json.dumps(
                    {
                        "meta": {
                            "project_id": project_id_arg,
                            "title": "good",
                            "aspect_ratio": "9:16",
                            "resolution": "720x1280",
                            "burn_captions": False,
                            "include_voiceover": False,
                            "include_music": True,
                            "enable_motion": True,
                            "video_effects_style": "Ken Burns - Standard",
                            "music": {"path": str(pdir / "assets/music/bed.mp3"), "volume_db": -6, "ducking": {"enabled": False}},
                        },
                        "scenes": [
                            {"id": "s01", "image_path": str(pdir / "assets/images/s01.png"), "start": 0, "duration": 2}
                        ],
                    }
                ),
                encoding="utf-8",
            )
        return StepResult(project_id_arg, "timeline", StepStatus.COMPLETED, outputs={"timeline_path": str(timeline_path)})

    monkeypatch.setattr("src.workflow.services.run_sync_timeline", _fake_sync_timeline)
    monkeypatch.setattr("src.workflow.services.ensure_ffmpeg_exists", lambda: None)
    monkeypatch.setattr(
        "src.workflow.services.render_video_from_timeline",
        lambda timeline_path, output_path, **kwargs: output_path.parent.mkdir(parents=True, exist_ok=True) or output_path.write_bytes(b"mp4"),
    )

    result = run_render_video(
        project_id,
        PipelineOptions(aspect_ratio="9:16", include_subtitles=False, include_music=True, selected_music_track=str(pdir / "assets/music/bed.mp3"), include_voiceover=False),
    )

    assert result.status == StepStatus.COMPLETED
    assert call_count["sync"] >= 2
    assert result.outputs["preflight"]["timeline_rebuild_attempted"] is True
    assert result.outputs["preflight"]["timeline_rebuild_succeeded"] is True


def test_load_options_explicit_options_override_payload(tmp_path, monkeypatch):
    """Explicit pipeline_options values must win over saved payload for render settings."""
    monkeypatch.chdir(tmp_path)
    project_id = "svc-options-override"
    # Payload has 16:9, subtitles on, music off
    save_project_payload(
        project_id,
        {
            "project_id": project_id,
            "aspect_ratio": "16:9",
            "enable_subtitles": True,
            "enable_music": False,
            "selected_music_track": "",
            "enable_video_effects": True,
            "video_effects_style": "Ken Burns - Standard",
            "music_volume_relative_to_voiceover": 0.5,
        },
    )

    from src.workflow.services import _load_options

    # User selects 9:16, subtitles off, music on via pipeline_options
    explicit_options = PipelineOptions(
        aspect_ratio="9:16",
        include_subtitles=False,
        include_music=True,
        selected_music_track="data/music_library/track.mp3",
        enable_video_effects=True,
        video_effects_style="Ken Burns - Dramatic",
        music_volume_relative_to_voiceover=0.3,
    )
    _, options = _load_options(project_id, explicit_options)

    assert options.aspect_ratio == "9:16", "aspect_ratio from options must override payload"
    assert options.include_subtitles is False, "include_subtitles from options must override payload"
    assert options.include_music is True, "include_music from options must override payload"
    assert options.selected_music_track == "data/music_library/track.mp3"
    assert options.video_effects_style == "Ken Burns - Dramatic"
    assert abs(options.music_volume_relative_to_voiceover - 0.3) < 0.001


def test_resolve_automation_render_settings_prefers_current_run_values() -> None:
    from src.workflow.services import resolve_automation_render_settings

    resolved = resolve_automation_render_settings(
        project_id="p1",
        workflow_state={"aspect_ratio": "16:9", "enable_subtitles": True, "enable_music": False},
        project_state={"aspect_ratio": "16:9", "enable_subtitles": True, "enable_music": False},
        session_state={
            "aspect_ratio": "9:16",
            "enable_subtitles": False,
            "enable_video_effects": True,
            "video_effects_style": "Ken Burns - Dramatic",
            "enable_music": True,
            "selected_music_track": "data/music/track.mp3",
        },
    )

    assert resolved.aspect_ratio == "9:16"
    assert resolved.output_size == "720x1280"
    assert resolved.subtitles_enabled is False
    assert resolved.effects_style == "Ken Burns - Dramatic"
    assert resolved.music_enabled is True
    assert resolved.music_track == "data/music/track.mp3"


def test_should_apply_subtitles_uses_resolved_settings() -> None:
    from src.workflow.services import ResolvedAutomationRenderSettings, should_apply_subtitles

    resolved = ResolvedAutomationRenderSettings(
        aspect_ratio="9:16",
        output_width=720,
        output_height=1280,
        output_size="720x1280",
        subtitles_enabled=False,
        effects_style="Ken Burns - Standard",
        music_enabled=False,
        music_track="",
    )

    assert should_apply_subtitles(resolved, timeline_meta=None) is False
