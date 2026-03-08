"""Workflow-specific exceptions."""


class WorkflowError(Exception):
    """Base exception for workflow failures."""


class WorkflowValidationError(WorkflowError):
    """Raised when workflow data fails validation constraints."""


class UnknownWorkflowStepError(WorkflowValidationError):
    """Raised when a provided step is not part of the workflow pipeline."""
