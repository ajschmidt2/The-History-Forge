import json
from pathlib import Path

from src.workflow.models import StepStatus
from src.workflow.state import (
    get_project_manifest,
    load_workflow_state,
    reset_downstream_steps,
    update_step_status,
)


def test_workflow_state_defaults_and_persistence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "demo-project"

    state = load_workflow_state(project_id)
    assert state.project_id == project_id
    assert state.current_stage == "script"
    assert state.step_statuses["script"] == StepStatus.NOT_STARTED

    updated = update_step_status(project_id, "script", StepStatus.COMPLETED)
    assert updated.step_statuses["script"] == StepStatus.COMPLETED

    reloaded = load_workflow_state(project_id)
    assert reloaded.step_statuses["script"] == StepStatus.COMPLETED


def test_reset_downstream_steps(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "demo-project"

    update_step_status(project_id, "script", StepStatus.COMPLETED)
    update_step_status(project_id, "scenes", StepStatus.COMPLETED)
    update_step_status(project_id, "prompts", StepStatus.COMPLETED)

    reset = reset_downstream_steps(project_id, "scenes")
    assert reset.step_statuses["script"] == StepStatus.COMPLETED
    assert reset.step_statuses["scenes"] == StepStatus.COMPLETED
    assert reset.step_statuses["prompts"] == StepStatus.NOT_STARTED


def test_project_manifest_creation_and_recovery(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "manifest-project"

    manifest = get_project_manifest(project_id)
    manifest_path = Path("data/projects") / project_id / "project_manifest.json"
    assert manifest_path.exists()
    assert manifest.timeline.endswith("timeline.json")

    manifest_path.write_text("{malformed", encoding="utf-8")
    recovered = get_project_manifest(project_id)
    assert recovered.project_id == project_id

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["project_id"] == project_id
