from pathlib import Path

from src.workflow.models import StepStatus
from src.workflow.project_io import load_scenes, save_scenes
from src.workflow.services import (
    FullWorkflowOptions,
    FullWorkflowResult,
    StepResult,
    run_full_workflow,
)
from utils import Scene


def test_run_full_workflow_stops_on_failed_critical_step(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        "src.workflow.services.run_generate_script",
        lambda project_id, options=None: StepResult(project_id, "script", StepStatus.COMPLETED),
    )
    monkeypatch.setattr(
        "src.workflow.services.run_split_scenes",
        lambda project_id, options=None: StepResult(project_id, "scenes", StepStatus.FAILED, message="scene split failed"),
    )

    result = run_full_workflow("wf-critical", FullWorkflowOptions(mode="full_auto"))

    assert isinstance(result, FullWorkflowResult)
    assert result.failed_step == "scenes"
    assert "script" in result.completed_steps
    assert "scene split failed" in " ".join(result.warnings)


def test_run_full_workflow_ai_video_falls_back_to_images(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "wf-ai-fallback"

    scenes = [
        Scene(index=1, title="S1", script_excerpt="One", visual_intent="V1", image_prompt="Prompt 1"),
        Scene(index=2, title="S2", script_excerpt="Two", visual_intent="V2", image_prompt="Prompt 2"),
    ]
    save_scenes(project_id, scenes)

    videos_dir = Path("data/projects") / project_id / "assets/videos"

    def _fake_generate_video(prompt, provider, project_id, aspect_ratio="16:9", save_dir=None, seconds=8):
        if "Prompt 2" in prompt:
            raise RuntimeError("provider timeout")
        save_path = Path(save_dir or videos_dir)
        save_path.mkdir(parents=True, exist_ok=True)
        target = save_path / "scene01.mp4"
        target.write_bytes(b"video-bytes")
        return "https://example.com/video.mp4", str(target)

    def _fake_step_exists(pid, step):
        return step in {"script", "scenes", "prompts", "images", "voiceover", "voiceover_timing", "timeline"}

    monkeypatch.setattr("src.workflow.services.generate_video", _fake_generate_video)
    monkeypatch.setattr("src.workflow.services._step_outputs_exist", _fake_step_exists)
    monkeypatch.setattr(
        "src.workflow.services.run_render_video",
        lambda project_id, options=None: StepResult(project_id, "render", StepStatus.COMPLETED, outputs={"video_path": "renders/final.mp4"}),
    )

    result = run_full_workflow(
        project_id,
        FullWorkflowOptions(
            mode="full_auto",
            enable_ai_video=True,
            ai_video_scene_indexes=[1, 2],
        ),
    )

    refreshed = load_scenes(project_id)
    assert result.failed_step == ""
    assert "ai_video" in result.completed_steps
    assert "render" in result.completed_steps
    assert any("using image fallback" in warning for warning in result.warnings)
    assert any(str(getattr(scene, "video_path", "")).endswith("scene01.mp4") for scene in refreshed)
