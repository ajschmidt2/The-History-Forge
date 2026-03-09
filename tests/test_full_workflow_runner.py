from src.workflow.models import StepStatus
from src.workflow.project_io import load_project_payload, save_project_payload, save_scenes
from src.workflow.services import (
    FullWorkflowOptions,
    FullWorkflowResult,
    PipelineOptions,
    StepResult,
    run_full_workflow,
)
from utils import Scene


def test_run_full_workflow_stops_on_failed_critical_step(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    project_id = "wf-critical"
    payload = load_project_payload(project_id)
    payload["script_text"] = "Existing script."
    save_project_payload(project_id, payload)

    monkeypatch.setattr(
        "src.workflow.services.run_generate_voiceover",
        lambda project_id, options=None: StepResult(project_id, "voiceover", StepStatus.COMPLETED),
    )
    monkeypatch.setattr(
        "src.workflow.services.run_split_scenes",
        lambda project_id, options=None: StepResult(project_id, "scenes", StepStatus.FAILED, message="scene split failed"),
    )

    result = run_full_workflow(project_id, FullWorkflowOptions(mode="full_auto", overwrite_scenes=True, overwrite_prompts=True, overwrite_images=True, overwrite_timeline=True, overwrite_render=True, overwrite_voiceover=True, pipeline=PipelineOptions(automation_mode="existing_script_full_workflow")))

    assert isinstance(result, FullWorkflowResult)
    assert result.failed_step == "scenes"
    assert "voiceover" in result.completed_steps
    assert "scene split failed" in " ".join(result.warnings)


def test_run_full_workflow_runs_new_automation_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "wf-new-order"

    scenes = [
        Scene(index=1, title="S1", script_excerpt="One", visual_intent="V1", image_prompt="Prompt 1"),
        Scene(index=2, title="S2", script_excerpt="Two", visual_intent="V2", image_prompt="Prompt 2"),
    ]
    save_scenes(project_id, scenes)

    payload = load_project_payload(project_id)
    payload["script_text"] = "Script text"
    save_project_payload(project_id, payload)

    execution_order: list[str] = []

    monkeypatch.setattr("src.workflow.services.run_generate_voiceover", lambda project_id, options=None: execution_order.append("voiceover") or StepResult(project_id, "voiceover", StepStatus.COMPLETED))
    monkeypatch.setattr("src.workflow.services.run_split_scenes", lambda project_id, options=None: execution_order.append("scenes") or StepResult(project_id, "scenes", StepStatus.COMPLETED))
    monkeypatch.setattr("src.workflow.services.run_apply_scene_narrative", lambda project_id, options=None: execution_order.append("narrative") or StepResult(project_id, "narrative", StepStatus.COMPLETED))
    monkeypatch.setattr("src.workflow.services.run_generate_prompts", lambda project_id, options=None: execution_order.append("prompts") or StepResult(project_id, "prompts", StepStatus.COMPLETED))
    monkeypatch.setattr("src.workflow.services.run_generate_images", lambda project_id, options=None: execution_order.append("images") or StepResult(project_id, "images", StepStatus.COMPLETED))
    monkeypatch.setattr("src.workflow.services.run_apply_video_effects", lambda project_id, options=None: execution_order.append("effects") or StepResult(project_id, "effects", StepStatus.COMPLETED))
    monkeypatch.setattr("src.workflow.services.run_render_video", lambda project_id, options=None: execution_order.append("render") or StepResult(project_id, "render", StepStatus.COMPLETED, outputs={"video_path": "renders/final.mp4"}))

    result = run_full_workflow(project_id, FullWorkflowOptions(mode="full_auto", overwrite_scenes=True, overwrite_prompts=True, overwrite_images=True, overwrite_timeline=True, overwrite_render=True, overwrite_voiceover=True, pipeline=PipelineOptions(automation_mode="existing_script_full_workflow")))

    assert result.failed_step == ""
    assert execution_order == ["voiceover", "scenes", "narrative", "prompts", "images", "effects", "render"]


def test_run_full_workflow_topic_mode_includes_script_step(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "wf-topic-order"

    payload = load_project_payload(project_id)
    payload["topic"] = "Roman roads"
    save_project_payload(project_id, payload)

    execution_order: list[str] = []

    monkeypatch.setattr("src.workflow.services.run_generate_short_script", lambda project_id, options=None: execution_order.append("script") or StepResult(project_id, "script", StepStatus.COMPLETED, outputs={"word_count": 150}))
    monkeypatch.setattr("src.workflow.services.run_generate_voiceover", lambda project_id, options=None: execution_order.append("voiceover") or StepResult(project_id, "voiceover", StepStatus.COMPLETED))
    monkeypatch.setattr("src.workflow.services.run_split_scenes", lambda project_id, options=None: execution_order.append("scenes") or StepResult(project_id, "scenes", StepStatus.COMPLETED))
    monkeypatch.setattr("src.workflow.services.run_apply_scene_narrative", lambda project_id, options=None: execution_order.append("narrative") or StepResult(project_id, "narrative", StepStatus.COMPLETED))
    monkeypatch.setattr("src.workflow.services.run_generate_prompts", lambda project_id, options=None: execution_order.append("prompts") or StepResult(project_id, "prompts", StepStatus.COMPLETED))
    monkeypatch.setattr("src.workflow.services.run_generate_images", lambda project_id, options=None: execution_order.append("images") or StepResult(project_id, "images", StepStatus.COMPLETED))
    monkeypatch.setattr("src.workflow.services.run_apply_video_effects", lambda project_id, options=None: execution_order.append("effects") or StepResult(project_id, "effects", StepStatus.COMPLETED))
    monkeypatch.setattr("src.workflow.services.run_render_video", lambda project_id, options=None: execution_order.append("render") or StepResult(project_id, "render", StepStatus.COMPLETED))

    result = run_full_workflow(project_id, FullWorkflowOptions(mode="full_auto", pipeline=PipelineOptions(automation_mode="topic_to_short_video", topic="Roman roads")))
    assert result.failed_step == ""
    assert execution_order == ["script", "voiceover", "scenes", "narrative", "prompts", "images", "effects", "render"]
