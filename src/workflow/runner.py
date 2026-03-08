"""Synchronous workflow runner for project production pipelines."""

from __future__ import annotations

from collections.abc import Iterable

from src.workflow.errors import UnknownWorkflowStepError, WorkflowError
from src.workflow.models import PIPELINE_STEPS, StepStatus, WorkflowState
from src.workflow.state import load_workflow_state, update_step_status
from src.workflow.steps import WorkflowStep


class WorkflowRunner:
    """Run a project workflow end-to-end while persisting step status updates."""

    def __init__(self, steps: Iterable[WorkflowStep]):
        self._steps = {step.name: step for step in steps}

    def run(self, project_id: str, start_from: str | None = None) -> WorkflowState:
        """Execute configured steps in pipeline order, beginning at ``start_from`` if provided."""
        state = load_workflow_state(project_id)
        start_index = 0
        if start_from:
            if start_from not in PIPELINE_STEPS:
                raise UnknownWorkflowStepError(f"Unknown workflow step: {start_from}")
            start_index = PIPELINE_STEPS.index(start_from)

        for step_name in PIPELINE_STEPS[start_index:]:
            step = self._steps.get(step_name)
            if step is None:
                continue
            update_step_status(project_id, step_name, StepStatus.IN_PROGRESS)
            try:
                step.handler(project_id)
            except WorkflowError as exc:
                state = update_step_status(project_id, step_name, StepStatus.FAILED, error=str(exc))
                return state
            except Exception as exc:  # noqa: BLE001 - safeguard for pipeline durability.
                state = update_step_status(project_id, step_name, StepStatus.FAILED, error=str(exc))
                return state
            state = update_step_status(project_id, step_name, StepStatus.COMPLETED)
        return state
