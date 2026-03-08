"""Workflow step definitions and registry helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.workflow.models import PIPELINE_STEPS

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
