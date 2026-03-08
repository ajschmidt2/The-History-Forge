"""Workflow step definitions and registry helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.workflow.models import PIPELINE_STEPS
from src.workflow.services import (
    run_apply_scene_narrative,
    run_apply_video_effects,
    run_generate_images,
    run_generate_prompts,
    run_generate_script,
    run_generate_voiceover,
    run_render_video,
    run_split_scenes,
    run_sync_timeline,
)

WorkflowStepHandler = Callable[[str], dict[str, Any] | None]


@dataclass(slots=True)
class WorkflowStep:
    """A named workflow step and its execution handler."""

    name: str
    handler: WorkflowStepHandler


def noop_step_handler(project_id: str) -> dict[str, Any]:
    """Default no-op handler used for steps not yet automated."""
    return {"project_id": project_id}


def default_steps() -> list[WorkflowStep]:
    """Return the default ordered workflow steps for the pipeline."""
    return [WorkflowStep(name=step_name, handler=noop_step_handler) for step_name in PIPELINE_STEPS]


def automation_steps() -> list[WorkflowStep]:
    """Return step handlers wired to reusable workflow service functions."""

    mapping: dict[str, WorkflowStepHandler] = {
        "script": lambda project_id: run_generate_script(project_id).outputs,
        "voiceover": lambda project_id: run_generate_voiceover(project_id).outputs,
        "scenes": lambda project_id: run_split_scenes(project_id).outputs,
        "narrative": lambda project_id: run_apply_scene_narrative(project_id).outputs,
        "prompts": lambda project_id: run_generate_prompts(project_id).outputs,
        "images": lambda project_id: run_generate_images(project_id).outputs,
        "ai_video": noop_step_handler,
        "music": noop_step_handler,
        "effects": lambda project_id: run_apply_video_effects(project_id).outputs,
        "timeline": lambda project_id: run_sync_timeline(project_id).outputs,
        "render": lambda project_id: run_render_video(project_id).outputs,
    }
    return [WorkflowStep(name=name, handler=mapping[name]) for name in PIPELINE_STEPS]
