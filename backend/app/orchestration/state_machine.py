"""Task lifecycle state machine with validated transitions.

Defines all valid state transitions for tasks, used by the scheduler,
override system, and DAG engine to enforce correct lifecycle behavior.
"""
from __future__ import annotations

from app.exceptions import ValidationError
from app.models.base import TaskStatus


# Map of (current_status, action) → new_status
# This is the single source of truth for all task state transitions.
VALID_TRANSITIONS: dict[tuple[TaskStatus, str], TaskStatus] = {
    # --- Normal orchestration flow ---
    (TaskStatus.TODO, "enqueue"):          TaskStatus.PENDING,
    (TaskStatus.PENDING, "assign"):        TaskStatus.ASSIGNED,
    (TaskStatus.ASSIGNED, "start"):        TaskStatus.RUNNING,
    (TaskStatus.PENDING, "start"):         TaskStatus.RUNNING,   # scheduler skips ASSIGNED
    (TaskStatus.RUNNING, "complete"):      TaskStatus.COMPLETED,
    (TaskStatus.RUNNING, "fail"):          TaskStatus.FAILED,
    (TaskStatus.RUNNING, "retry"):         TaskStatus.RETRYING,
    (TaskStatus.RETRYING, "start"):        TaskStatus.RUNNING,
    (TaskStatus.RETRYING, "fail"):         TaskStatus.FAILED,
    (TaskStatus.PENDING, "skip"):          TaskStatus.SKIPPED,
    (TaskStatus.PENDING, "block"):         TaskStatus.BLOCKED,
    (TaskStatus.BLOCKED, "unblock"):       TaskStatus.PENDING,

    # --- Manual override actions ---
    (TaskStatus.FAILED, "retry"):          TaskStatus.PENDING,
    (TaskStatus.BLOCKED, "retry"):         TaskStatus.PENDING,
    (TaskStatus.RUNNING, "skip"):          TaskStatus.SKIPPED,
    (TaskStatus.BLOCKED, "skip"):          TaskStatus.SKIPPED,
    (TaskStatus.TODO, "skip"):             TaskStatus.SKIPPED,
    (TaskStatus.RUNNING, "force_complete"): TaskStatus.COMPLETED,
    (TaskStatus.FAILED, "force_complete"):  TaskStatus.COMPLETED,
    (TaskStatus.PENDING, "force_complete"): TaskStatus.COMPLETED,
    (TaskStatus.BLOCKED, "force_complete"): TaskStatus.COMPLETED,
    (TaskStatus.TODO, "force_complete"):    TaskStatus.COMPLETED,
    (TaskStatus.PENDING, "cancel"):        TaskStatus.FAILED,
    (TaskStatus.RUNNING, "cancel"):        TaskStatus.FAILED,
    (TaskStatus.BLOCKED, "cancel"):        TaskStatus.FAILED,
    (TaskStatus.TODO, "cancel"):           TaskStatus.FAILED,
    (TaskStatus.ASSIGNED, "cancel"):       TaskStatus.FAILED,

    # --- Dependency-driven transitions ---
    (TaskStatus.PENDING, "dep_failed"):    TaskStatus.SKIPPED,
    (TaskStatus.TODO, "dep_failed"):       TaskStatus.SKIPPED,
    (TaskStatus.BLOCKED, "dep_failed"):    TaskStatus.SKIPPED,
}

# Terminal states — tasks in these states cannot transition further (except via override)
TERMINAL_STATES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED}

# Override-allowed states — states that can be overridden by manual action
OVERRIDE_ACTIONS = {"retry", "skip", "force_complete", "cancel"}


def validate_transition(current: TaskStatus, action: str) -> TaskStatus:
    """Validate and return the target status for a state transition.

    Args:
        current: The task's current status.
        action: The action being performed.

    Returns:
        The new TaskStatus after the transition.

    Raises:
        ValidationError: If the transition is not allowed.
    """
    key = (current, action)
    new_status = VALID_TRANSITIONS.get(key)

    if new_status is None:
        raise ValidationError(
            f"Invalid transition: cannot perform '{action}' on task in '{current.value}' state. "
            f"Valid actions from '{current.value}': "
            f"{[a for (s, a) in VALID_TRANSITIONS if s == current]}"
        )

    return new_status


def is_terminal(status: TaskStatus) -> bool:
    """Check if a status is terminal (task execution is finished)."""
    return status in TERMINAL_STATES


def get_valid_actions(current: TaskStatus) -> list[str]:
    """Get all valid actions for a given status."""
    return sorted({action for (status, action) in VALID_TRANSITIONS if status == current})


def get_valid_override_actions(current: TaskStatus) -> list[str]:
    """Get valid manual override actions for a given status."""
    return sorted(
        action
        for (status, action) in VALID_TRANSITIONS
        if status == current and action in OVERRIDE_ACTIONS
    )
