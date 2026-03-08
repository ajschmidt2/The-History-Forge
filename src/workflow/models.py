"""Data models for durable project workflow orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

PIPELINE_STEPS: tuple[str, ...] = (
    "script",
    "voiceover",
    "scenes",
    "narrative",
    "prompts",
    "images",
    "ai_video",
    "music",
    "effects",
    "timeline",
    "render",
)


class StepStatus(StrEnum):
    """Supported lifecycle values for each step in the workflow."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    SKIPPED = "skipped"


class WorkflowStatus(StrEnum):
    """High-level status for the entire workflow."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


@dataclass(slots=True)
class WorkflowState:
    """Persistent workflow state for a single project."""

    project_id: str
    current_stage: str = "script"
    overall_status: WorkflowStatus = WorkflowStatus.NOT_STARTED
    step_statuses: dict[str, StepStatus] = field(default_factory=dict)
    timestamps: dict[str, str] = field(default_factory=dict)
    last_error: str = ""
    retry_counts: dict[str, int] = field(default_factory=dict)
    asset_summary: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for step in PIPELINE_STEPS:
            self.step_statuses.setdefault(step, StepStatus.NOT_STARTED)
            self.retry_counts.setdefault(step, 0)

    def to_dict(self) -> dict[str, Any]:
        """Serialize workflow state to a JSON-safe dictionary."""
        return {
            "project_id": self.project_id,
            "current_stage": self.current_stage,
            "overall_status": self.overall_status.value,
            "step_statuses": {name: status.value for name, status in self.step_statuses.items()},
            "timestamps": dict(self.timestamps),
            "last_error": self.last_error,
            "retry_counts": dict(self.retry_counts),
            "asset_summary": dict(self.asset_summary),
        }

    @classmethod
    def from_dict(cls, payload: object, project_id: str) -> "WorkflowState":
        """Create a validated state object from raw JSON data."""
        if not isinstance(payload, dict):
            return cls(project_id=project_id)

        raw_step_statuses = payload.get("step_statuses", {})
        step_statuses: dict[str, StepStatus] = {}
        if isinstance(raw_step_statuses, dict):
            for step in PIPELINE_STEPS:
                raw_status = str(raw_step_statuses.get(step, StepStatus.NOT_STARTED.value) or StepStatus.NOT_STARTED.value)
                try:
                    step_statuses[step] = StepStatus(raw_status)
                except ValueError:
                    step_statuses[step] = StepStatus.NOT_STARTED

        raw_retry_counts = payload.get("retry_counts", {})
        retry_counts: dict[str, int] = {}
        if isinstance(raw_retry_counts, dict):
            for step in PIPELINE_STEPS:
                raw_retry = raw_retry_counts.get(step, 0)
                try:
                    retry_counts[step] = max(0, int(raw_retry or 0))
                except (TypeError, ValueError):
                    retry_counts[step] = 0

        raw_timestamps = payload.get("timestamps", {})
        timestamps = raw_timestamps if isinstance(raw_timestamps, dict) else {}

        raw_asset_summary = payload.get("asset_summary", {})
        asset_summary = raw_asset_summary if isinstance(raw_asset_summary, dict) else {}

        raw_overall_status = str(payload.get("overall_status", WorkflowStatus.NOT_STARTED.value) or WorkflowStatus.NOT_STARTED.value)
        try:
            overall_status = WorkflowStatus(raw_overall_status)
        except ValueError:
            overall_status = WorkflowStatus.NOT_STARTED

        current_stage = str(payload.get("current_stage", "script") or "script")
        if current_stage not in PIPELINE_STEPS:
            current_stage = "script"

        return cls(
            project_id=str(payload.get("project_id", project_id) or project_id),
            current_stage=current_stage,
            overall_status=overall_status,
            step_statuses=step_statuses,
            timestamps={str(k): str(v) for k, v in timestamps.items()},
            last_error=str(payload.get("last_error", "") or ""),
            retry_counts=retry_counts,
            asset_summary=asset_summary,
        )


@dataclass(slots=True)
class ProjectManifest:
    """Canonical per-project file/folder pointers for workflow automation."""

    project_id: str
    script: str
    scenes: str
    images: str
    videos: str
    audio: str
    music: str
    timeline: str
    final_render: str

    def to_dict(self) -> dict[str, str]:
        return {
            "project_id": self.project_id,
            "script": self.script,
            "scenes": self.scenes,
            "images": self.images,
            "videos": self.videos,
            "audio": self.audio,
            "music": self.music,
            "timeline": self.timeline,
            "final_render": self.final_render,
        }

    @classmethod
    def default(cls, project_id: str) -> "ProjectManifest":
        base = f"data/projects/{project_id}"
        return cls(
            project_id=project_id,
            script=f"{base}/script.txt",
            scenes=f"{base}/scenes.json",
            images=f"{base}/assets/images",
            videos=f"{base}/assets/videos",
            audio=f"{base}/assets/audio",
            music=f"{base}/assets/music",
            timeline=f"{base}/timeline.json",
            final_render=f"{base}/final_render.mp4",
        )


def now_iso() -> str:
    """Return a UTC timestamp in ISO8601 format."""
    return datetime.now(UTC).isoformat()
