"""Durable workflow and manifest persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path

from src.workflow.errors import UnknownWorkflowStepError
from src.workflow.models import PIPELINE_STEPS, ProjectManifest, StepStatus, WorkflowState, WorkflowStatus, now_iso

PROJECTS_ROOT = Path("data/projects")
WORKFLOW_STATE_FILENAME = "workflow_state.json"
PROJECT_MANIFEST_FILENAME = "project_manifest.json"


def _project_dir(project_id: str) -> Path:
    normalized = str(project_id or "").strip()
    return PROJECTS_ROOT / normalized


def _workflow_state_path(project_id: str) -> Path:
    return _project_dir(project_id) / WORKFLOW_STATE_FILENAME


def _project_manifest_path(project_id: str) -> Path:
    return _project_dir(project_id) / PROJECT_MANIFEST_FILENAME


def _ensure_project_layout(project_id: str) -> None:
    project_dir = _project_dir(project_id)
    (project_dir / "assets/images").mkdir(parents=True, exist_ok=True)
    (project_dir / "assets/audio").mkdir(parents=True, exist_ok=True)
    (project_dir / "assets/music").mkdir(parents=True, exist_ok=True)
    (project_dir / "assets/videos").mkdir(parents=True, exist_ok=True)


def load_workflow_state(project_id: str) -> WorkflowState:
    """Load workflow state for a project with safe defaults for malformed files."""
    _ensure_project_layout(project_id)
    state_path = _workflow_state_path(project_id)
    if not state_path.exists():
        state = WorkflowState(project_id=project_id)
        save_workflow_state(project_id, state)
        return state

    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = WorkflowState(project_id=project_id)
        save_workflow_state(project_id, state)
        return state

    state = WorkflowState.from_dict(raw, project_id=project_id)
    if not state.project_id:
        state.project_id = project_id
    return state


def save_workflow_state(project_id: str, state: WorkflowState) -> None:
    """Persist workflow state JSON to disk."""
    _ensure_project_layout(project_id)
    state.project_id = project_id
    state_path = _workflow_state_path(project_id)
    state_path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def _derive_overall_status(step_statuses: dict[str, StepStatus]) -> WorkflowStatus:
    if any(status == StepStatus.FAILED for status in step_statuses.values()):
        return WorkflowStatus.FAILED
    if all(status in (StepStatus.COMPLETED, StepStatus.SKIPPED) for status in step_statuses.values()):
        return WorkflowStatus.COMPLETED
    if any(status == StepStatus.NEEDS_REVIEW for status in step_statuses.values()):
        return WorkflowStatus.NEEDS_REVIEW
    if any(status != StepStatus.NOT_STARTED for status in step_statuses.values()):
        return WorkflowStatus.IN_PROGRESS
    return WorkflowStatus.NOT_STARTED


def update_step_status(project_id: str, step_name: str, status: StepStatus, error: str | None = None) -> WorkflowState:
    """Update a single step status and persist the workflow state."""
    if step_name not in PIPELINE_STEPS:
        raise UnknownWorkflowStepError(f"Unknown workflow step: {step_name}")

    state = load_workflow_state(project_id)
    state.current_stage = step_name
    state.step_statuses[step_name] = status
    state.timestamps[step_name] = now_iso()

    if error:
        state.last_error = error
    elif status != StepStatus.FAILED:
        state.last_error = ""

    if status == StepStatus.FAILED:
        state.retry_counts[step_name] = state.retry_counts.get(step_name, 0) + 1

    state.overall_status = _derive_overall_status(state.step_statuses)
    save_workflow_state(project_id, state)
    return state


def reset_downstream_steps(project_id: str, from_step: str) -> WorkflowState:
    """Reset all steps after ``from_step`` so the pipeline can be replayed safely."""
    if from_step not in PIPELINE_STEPS:
        raise UnknownWorkflowStepError(f"Unknown workflow step: {from_step}")

    state = load_workflow_state(project_id)
    reset_started = False
    for step in PIPELINE_STEPS:
        if step == from_step:
            reset_started = True
            continue
        if reset_started:
            state.step_statuses[step] = StepStatus.NOT_STARTED
            state.timestamps.pop(step, None)
            state.retry_counts[step] = 0

    state.current_stage = from_step
    state.last_error = ""
    state.overall_status = _derive_overall_status(state.step_statuses)
    save_workflow_state(project_id, state)
    return state


def get_project_manifest(project_id: str) -> ProjectManifest:
    """Load (or generate) a durable per-project manifest of canonical asset paths."""
    _ensure_project_layout(project_id)
    manifest_path = _project_manifest_path(project_id)
    default_manifest = ProjectManifest.default(project_id)

    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(default_manifest.to_dict(), indent=2), encoding="utf-8")
        return default_manifest

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest_path.write_text(json.dumps(default_manifest.to_dict(), indent=2), encoding="utf-8")
        return default_manifest

    if not isinstance(raw, dict):
        manifest_path.write_text(json.dumps(default_manifest.to_dict(), indent=2), encoding="utf-8")
        return default_manifest

    merged = default_manifest.to_dict()
    for key in merged:
        if key in raw and isinstance(raw[key], str) and raw[key].strip():
            merged[key] = raw[key]

    manifest = ProjectManifest(**merged)
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    return manifest
