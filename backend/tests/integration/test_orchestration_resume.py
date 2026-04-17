"""Integration tests for :class:`WorkflowResumer` — the Day-13 cross-node resume primitive.

Scenario structure mirrors the federation demo:
- ``original`` session = node-A that wrote the checkpoint before pausing.
- ``resumer`` session = node-B that took over after node-A died. It must
  rebuild state from the signed checkpoint without any direct access to
  node-A's Workflow / Task rows.

These tests use the shared ``db_session`` fixture as a stand-in for both
nodes (they share the same Postgres instance but operate on workflow ids
that may or may not have local rows — which is exactly the state we
reconcile).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.identity.signer import generate_keypair
from app.models.base import TaskStatus, WorkflowStatus
from app.models.task import Task
from app.models.workflow import Workflow
from app.orchestration.checkpoint import CheckpointStore, WorkflowCheckpoint
from app.orchestration.dag import DAG, DAGNode
from app.orchestration.resume import ResumeError, WorkflowResumer


def _dag() -> DAG:
    return DAG(
        [
            DAGNode(
                node_id="task-1",
                capability="research",
                description="scope the problem",
                depends_on=[],
                status=TaskStatus.COMPLETED,
            ),
            DAGNode(
                node_id="task-2",
                capability="writing",
                description="draft the response",
                depends_on=["task-1"],
                status=TaskStatus.PENDING,
            ),
            DAGNode(
                node_id="task-3",
                capability="review",
                description="check quality",
                depends_on=["task-2"],
                status=TaskStatus.PENDING,
            ),
        ]
    )


@pytest_asyncio.fixture
async def keypair():
    return generate_keypair()


@pytest_asyncio.fixture
async def seeded_workflow(db_session):
    wf = Workflow(
        user_id="demo",
        prompt="resume me",
        budget_limit=100.0,
        status=WorkflowStatus.RUNNING,
        dag_snapshot=_dag().to_dict(),
    )
    db_session.add(wf)
    await db_session.flush()
    # Pre-seed task-1 as COMPLETED so we can verify restore preserves it.
    t1 = Task(
        workflow_id=wf.workflow_id,
        step_number=1,
        capability="research",
        description="scope the problem",
        status=TaskStatus.COMPLETED,
    )
    db_session.add(t1)
    await db_session.flush()
    return wf


class TestResumeOnLocalNode:
    @pytest.mark.asyncio
    async def test_load_and_restore_preserves_bucket_status(self, db_session, redis_client, keypair, seeded_workflow):
        """Round-trip: write checkpoint on this session, load it back, restore patches task statuses."""
        store = CheckpointStore(session=db_session, keypair=keypair)
        cp = WorkflowCheckpoint.from_dag(
            workflow_id=seeded_workflow.workflow_id,
            dag=_dag(),
            reason="awaiting_approval: review",
            current_task_id="task-2",
        )
        saved = await store.save(cp)
        assert saved.content_hash is not None

        resumer = WorkflowResumer(session=db_session, keypair=keypair)
        loaded = await resumer.load(saved.content_hash)
        assert loaded.workflow_id == seeded_workflow.workflow_id

        outcome = await resumer.restore(loaded)
        assert outcome.created_workflow is False
        # task-1 already existed — should be left COMPLETED.
        # task-2 and task-3 are created fresh.
        assert set(outcome.created_task_ids) == {"task-2", "task-3"}

        tasks = (
            (
                await db_session.execute(
                    select(Task).where(Task.workflow_id == seeded_workflow.workflow_id).order_by(Task.step_number)
                )
            )
            .scalars()
            .all()
        )
        assert len(tasks) == 3
        by_step = {t.step_number: t for t in tasks}
        assert by_step[1].status == TaskStatus.COMPLETED
        assert by_step[2].status == TaskStatus.PENDING
        assert by_step[3].status == TaskStatus.PENDING
        # Workflow row reflects that it was resumed.
        await db_session.refresh(seeded_workflow)
        assert seeded_workflow.status == WorkflowStatus.RUNNING
        assert seeded_workflow.metadata_["resumed_from_checkpoint"] == saved.content_hash


class TestResumeOnFreshNode:
    """Simulates node-B: workflow row exists (pre-seeded via the federation
    demo script or an earlier gossip tick) but no task rows yet — that's
    the real cross-node resume situation, where the *task-state delta*
    is what travels via the signed checkpoint while the workflow shell
    has already been replicated.
    """

    @pytest.mark.asyncio
    async def test_restore_creates_task_rows_from_checkpoint(self, db_session, redis_client, keypair):
        shared_workflow_id = uuid.uuid4()
        # Pre-seed a shadow Workflow row — mirrors what the federation
        # demo's seed script does and what gossip ultimately converges to.
        shadow = Workflow(
            workflow_id=shared_workflow_id,
            user_id="federation",
            prompt="resumed on fresh peer",
            status=WorkflowStatus.PAUSED,
            budget_limit=100.0,
        )
        db_session.add(shadow)
        await db_session.flush()

        store = CheckpointStore(session=db_session, keypair=keypair)
        cp = WorkflowCheckpoint.from_dag(
            workflow_id=shared_workflow_id,
            dag=_dag(),
            reason="awaiting_approval: review",
            current_task_id="task-2",
        )
        saved = await store.save(cp)

        resumer = WorkflowResumer(session=db_session, keypair=keypair)
        loaded = await resumer.load(saved.content_hash)
        outcome = await resumer.restore(loaded, default_prompt="resumed on fresh peer")

        # The workflow row already existed (pre-seeded) — restore patches
        # status and dag_snapshot but doesn't re-create it.
        assert outcome.created_workflow is False
        assert set(outcome.created_task_ids) == {"task-1", "task-2", "task-3"}

        wf = (await db_session.execute(select(Workflow).where(Workflow.workflow_id == shared_workflow_id))).scalar_one()
        assert wf.status == WorkflowStatus.RUNNING
        assert wf.dag_snapshot is not None

        tasks = (
            (
                await db_session.execute(
                    select(Task).where(Task.workflow_id == shared_workflow_id).order_by(Task.step_number)
                )
            )
            .scalars()
            .all()
        )
        assert len(tasks) == 3
        by_step = {t.step_number: t.status for t in tasks}
        assert by_step == {
            1: TaskStatus.COMPLETED,
            2: TaskStatus.PENDING,
            3: TaskStatus.PENDING,
        }


class TestResumeIsIdempotent:
    @pytest.mark.asyncio
    async def test_restoring_twice_converges_to_same_rows(self, db_session, redis_client, keypair):
        wid = uuid.uuid4()
        # Pre-seed the workflow row (as gossip / demo seed would).
        shadow = Workflow(
            workflow_id=wid,
            user_id="federation",
            prompt="idempotent resume",
            status=WorkflowStatus.PAUSED,
            budget_limit=100.0,
        )
        db_session.add(shadow)
        await db_session.flush()

        store = CheckpointStore(session=db_session, keypair=keypair)
        saved = await store.save(WorkflowCheckpoint.from_dag(workflow_id=wid, dag=_dag(), reason="awaiting_approval"))
        resumer = WorkflowResumer(session=db_session, keypair=keypair)

        loaded = await resumer.load(saved.content_hash)
        first = await resumer.restore(loaded)
        assert first.created_workflow is False
        assert len(first.created_task_ids) == 3

        second = await resumer.restore(loaded)
        assert second.created_workflow is False
        assert second.created_task_ids == ()
        assert second.updated_task_ids == ()

        row_count = (await db_session.execute(select(Task).where(Task.workflow_id == wid))).scalars().all()
        assert len(row_count) == 3


class TestResumeErrors:
    @pytest.mark.asyncio
    async def test_unknown_hash_raises(self, db_session, redis_client, keypair):
        resumer = WorkflowResumer(session=db_session, keypair=keypair)
        with pytest.raises(ResumeError):
            await resumer.load("z6MkUnknownHashThatWasNeverStored")

    @pytest.mark.asyncio
    async def test_empty_hash_raises(self, db_session, redis_client, keypair):
        resumer = WorkflowResumer(session=db_session, keypair=keypair)
        with pytest.raises(ResumeError):
            await resumer.load("")
