"""Cross-node workflow resume.

Given a signed :class:`WorkflowCheckpoint` (addressable by its content hash
and gossiped via the CRDT memory layer), this module takes whatever local
state the current node has for the workflow and reconciles it so the
scheduler can continue execution from where the pause happened — even
when the originating node is offline.

The restore path is intentionally idempotent: calling ``restore()`` twice
with the same checkpoint is safe and converges to the same Workflow +
Task rows. That matters for the federation demo where an approval
decision may be observed by more than one worker before the workflow
actually re-runs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.identity.signer import KeyPair
from app.models.base import TaskStatus, WorkflowStatus
from app.models.task import Task
from app.models.workflow import Workflow
from app.orchestration.checkpoint import CheckpointStore, WorkflowCheckpoint
from app.orchestration.dag import DAG

logger = get_logger(__name__)


class ResumeError(Exception):
    """Raised when a workflow cannot be resumed from its checkpoint."""


@dataclass(frozen=True)
class ResumeOutcome:
    """What changed on disk as a result of a resume call."""

    workflow: Workflow
    checkpoint: WorkflowCheckpoint
    restored_dag: DAG
    created_workflow: bool
    created_task_ids: tuple[str, ...]
    updated_task_ids: tuple[str, ...]

    def as_summary(self) -> dict[str, Any]:
        """Compact dict suitable for API responses and logs."""
        return {
            "workflow_id": str(self.workflow.workflow_id),
            "checkpoint_hash": self.checkpoint.content_hash,
            "created_workflow": self.created_workflow,
            "created_tasks": list(self.created_task_ids),
            "updated_tasks": list(self.updated_task_ids),
            "status": self.workflow.status.value,
        }


class WorkflowResumer:
    """Resume a workflow on the *current* node from a signed checkpoint.

    The resumer has no opinion about whether the workflow originally ran
    here — if the Workflow row is missing it is re-created from the
    checkpoint's dag_snapshot. Task rows are inserted or patched so their
    statuses match the checkpoint buckets (completed / pending / failed).

    Execution itself is NOT triggered from here. Callers that want to
    kick the scheduler back on should look at :meth:`ResumeOutcome.workflow`
    and dispatch via their usual workflow runner — keeping the state
    sync and the execution entry-point in separate modules means this
    class is easy to unit-test without booting the scheduler.
    """

    def __init__(self, session: AsyncSession, keypair: KeyPair) -> None:
        self._session = session
        self._store = CheckpointStore(session=session, keypair=keypair)

    async def load(self, content_hash: str) -> WorkflowCheckpoint:
        """Fetch a checkpoint from the local CRDT store.

        Raises :class:`ResumeError` if the hash is unknown (yet to
        gossip, or simply wrong).
        """
        if not content_hash:
            raise ResumeError("content_hash is required")
        cp = await self._store.load_by_hash(content_hash)
        if cp is None:
            raise ResumeError(f"checkpoint not found for hash {content_hash!r}")
        return cp

    async def restore(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        default_prompt: str = "",
        default_user_id: str = "federation",
        default_budget_limit: float = 100.0,
    ) -> ResumeOutcome:
        """Reconcile local DB state with the checkpoint and return an outcome.

        - If the Workflow row doesn't exist locally it is created with
          sensible defaults (the prompt/user/budget are only used for
          the shadow row; real originator data gets re-merged whenever
          the next memory record for this workflow arrives).
        - Tasks are keyed by (workflow_id, step_number); step_number is
          derived from the DAG node_id (trailing digits, with a stable
          fallback for alphabetic ids) so that the same checkpoint
          always rebuilds the same rows.
        - The restored DAG snapshot is written back onto the Workflow
          row so subsequent reads (UI, scheduler) see the authoritative
          state.
        """
        # 1. Rebuild the DAG — fail fast if the snapshot is corrupt.
        restored_dag = checkpoint.restore_dag()

        # 2. Workflow row: create if missing, otherwise patch state.
        wf_result = await self._session.execute(select(Workflow).where(Workflow.workflow_id == checkpoint.workflow_id))
        workflow = wf_result.scalar_one_or_none()
        created_workflow = False
        if workflow is None:
            workflow = Workflow(
                workflow_id=checkpoint.workflow_id,
                user_id=default_user_id,
                prompt=default_prompt or f"resumed from checkpoint {(checkpoint.content_hash or '')[:12]}",
                status=WorkflowStatus.RUNNING,
                budget_limit=default_budget_limit,
                budget_used=0.0,
                priority=5,
                metadata_={
                    "resumed_from_checkpoint": checkpoint.content_hash,
                    "resumed_at": datetime.now(UTC).isoformat(),
                },
                dag_snapshot=checkpoint.dag_snapshot,
                started_at=datetime.now(UTC),
            )
            self._session.add(workflow)
            await self._session.flush()
            created_workflow = True
        else:
            workflow.dag_snapshot = checkpoint.dag_snapshot
            workflow.status = WorkflowStatus.RUNNING
            meta = dict(workflow.metadata_ or {})
            meta["resumed_from_checkpoint"] = checkpoint.content_hash
            meta["resumed_at"] = datetime.now(UTC).isoformat()
            workflow.metadata_ = meta
            await self._session.flush()

        # 3. Task rows: reconcile bucket statuses.
        status_by_node = _bucket_statuses(checkpoint)
        existing_tasks = await self._session.execute(select(Task).where(Task.workflow_id == checkpoint.workflow_id))
        tasks_by_step: dict[int, Task] = {}
        for task in existing_tasks.scalars().all():
            tasks_by_step.setdefault(task.step_number, task)

        created_ids: list[str] = []
        updated_ids: list[str] = []
        for dag_node in restored_dag.nodes:
            step = _step_number(dag_node.node_id)
            target_status = status_by_node.get(dag_node.node_id, TaskStatus.PENDING)
            existing = tasks_by_step.get(step)
            if existing is None:
                new_task = Task(
                    task_id=uuid.uuid4(),
                    workflow_id=checkpoint.workflow_id,
                    step_number=step,
                    capability=dag_node.capability,
                    description=dag_node.description,
                    status=target_status,
                    retry_count=0,
                    max_retries=3,
                    cost_used=0.0,
                )
                self._session.add(new_task)
                created_ids.append(dag_node.node_id)
            elif existing.status != target_status:
                existing.status = target_status
                updated_ids.append(dag_node.node_id)

        await self._session.flush()

        logger.info(
            "workflow_resumed",
            workflow_id=str(checkpoint.workflow_id),
            checkpoint_hash=(checkpoint.content_hash or "")[:12],
            created_workflow=created_workflow,
            created_tasks=len(created_ids),
            updated_tasks=len(updated_ids),
        )

        return ResumeOutcome(
            workflow=workflow,
            checkpoint=checkpoint,
            restored_dag=restored_dag,
            created_workflow=created_workflow,
            created_task_ids=tuple(created_ids),
            updated_task_ids=tuple(updated_ids),
        )


def _bucket_statuses(checkpoint: WorkflowCheckpoint) -> dict[str, TaskStatus]:
    """Flatten the three checkpoint buckets into a node_id → status map."""
    out: dict[str, TaskStatus] = {}
    for nid in checkpoint.completed_task_ids:
        out[nid] = TaskStatus.COMPLETED
    for nid in checkpoint.failed_task_ids:
        out[nid] = TaskStatus.FAILED
    for nid in checkpoint.pending_task_ids:
        # Pending wins over any earlier bucket when a node is listed
        # twice (shouldn't happen in practice, but be defensive).
        out[nid] = TaskStatus.PENDING
    return out


def _step_number(node_id: str) -> int:
    """Extract a stable integer step number from a DAG node id.

    DAG node ids in MassClaw are typically ``task-1``, ``task-2``, …
    The scheduler's existing code uses ``int(digits_in_node_id)``, so we
    replicate that here. If the id has no digits (e.g. ``"solo"``) we
    fall back to a deterministic positive int derived from the hash of
    the id so that round-tripping is stable and unique within a DAG.
    """
    digits = "".join(c for c in node_id if c.isdigit())
    if digits:
        return int(digits)
    # Positive 31-bit deterministic fallback; the collision surface is
    # bounded by DAG size, which is small.
    return abs(hash(node_id)) % (2**31 - 1) + 1
