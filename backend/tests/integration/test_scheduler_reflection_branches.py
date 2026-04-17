"""End-to-end coverage for :meth:`WorkflowScheduler._reflect_on_task`.

This is the behavioural contract for the four-branch adaptive-intelligence
dispatch wired in on Day 12. Each test patches :class:`ReflectionEngine`
to return a specific action and then asserts the observable side effects
on the DAG, the Task row, and (for ``abort``) the persisted checkpoint.

Why integration-level: the scheduler touches real SQLAlchemy sessions
and the CRDT store, which is DB-bound. Mocking the intelligence layer
keeps each test deterministic and fast.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.identity.signer import generate_keypair
from app.intelligence.reflection import ReflectionResult
from app.models.base import TaskStatus, WorkflowStatus
from app.models.task import Task
from app.models.workflow import Workflow
from app.orchestration.checkpoint import CheckpointStore, is_checkpoint_record
from app.orchestration.dag import DAG, DAGNode
from app.orchestration.scheduler import WorkflowScheduler


def _dag() -> DAG:
    return DAG(
        [
            DAGNode(
                node_id="task-1",
                capability="research",
                description="investigate options",
                depends_on=[],
                status=TaskStatus.RUNNING,
            ),
            DAGNode(
                node_id="task-2",
                capability="writing",
                description="draft the reply",
                depends_on=["task-1"],
                status=TaskStatus.PENDING,
            ),
        ]
    )


@pytest_asyncio.fixture
async def workflow_and_task(db_session):
    wf = Workflow(
        user_id="demo",
        prompt="Build a reflection scenario end-to-end",
        budget_limit=100.0,
        status=WorkflowStatus.RUNNING,
        dag_snapshot=_dag().to_dict(),
    )
    db_session.add(wf)
    await db_session.flush()

    task = Task(
        workflow_id=wf.workflow_id,
        step_number=1,
        capability="research",
        description="investigate options",
        status=TaskStatus.RUNNING,
        output={},
        retry_count=0,
    )
    db_session.add(task)
    await db_session.flush()
    await db_session.refresh(task)
    return wf, task


@pytest_asyncio.fixture
async def instance_keypair_patched():
    """Serve a deterministic keypair so CheckpointStore can sign in tests."""
    keypair = generate_keypair()

    class _Store:
        def instance_keypair(self):
            return keypair

    with patch("app.services.identity_service.get_instance_key_store", return_value=_Store()):
        yield keypair


def _patched_reflection(action: str, *, confidence: float = 0.3, issues=None, suggestions=None):
    """Return a context manager that pins ReflectionEngine.reflect to a given action."""
    result = ReflectionResult(
        should_continue=action == "accept",
        confidence=confidence,
        action=action,
        issues=list(issues or []),
        suggestions=list(suggestions or []),
    )
    return patch(
        "app.intelligence.reflection.ReflectionEngine.reflect",
        new=AsyncMock(return_value=result),
    )


class TestAcceptBranch:
    @pytest.mark.asyncio
    async def test_accept_is_noop(self, db_session, redis_client, workflow_and_task):
        wf, task = workflow_and_task
        dag = DAG.from_dict(wf.dag_snapshot)
        node = dag.get_node("task-1")

        scheduler = WorkflowScheduler(session=db_session, redis=redis_client)
        with _patched_reflection("accept", confidence=0.95):
            outcome = await scheduler._reflect_on_task(
                workflow=wf, dag=dag, node=node, task_rec=task, response_content="looks great"
            )

        assert outcome is None
        assert task.status == TaskStatus.RUNNING  # unchanged
        assert dag.get_node("task-1").status == TaskStatus.RUNNING
        assert task.output["reflection"]["action"] == "accept"


class TestRetryBranch:
    @pytest.mark.asyncio
    async def test_retry_resets_node_and_signals_continue(self, db_session, redis_client, workflow_and_task):
        wf, task = workflow_and_task
        dag = DAG.from_dict(wf.dag_snapshot)
        node = dag.get_node("task-1")

        scheduler = WorkflowScheduler(session=db_session, redis=redis_client)
        with _patched_reflection("retry_task", confidence=0.3, issues=["too vague"], suggestions=["add citations"]):
            outcome = await scheduler._reflect_on_task(
                workflow=wf, dag=dag, node=node, task_rec=task, response_content="weak draft"
            )

        assert outcome is not None
        assert outcome["continue_loop"] is True
        assert outcome["action"] == "retry_task"
        assert task.status == TaskStatus.PENDING
        assert task.retry_count == 1
        assert dag.get_node("task-1").status == TaskStatus.PENDING
        assert "retry_feedback" in task.output
        assert task.output["reflection"]["action"] == "retry_task"

    @pytest.mark.asyncio
    async def test_retry_blocked_when_max_retries_exceeded(self, db_session, redis_client, workflow_and_task):
        wf, task = workflow_and_task
        task.retry_count = 5  # SelfCorrectionEngine caps retries; any large number refuses.
        await db_session.flush()

        dag = DAG.from_dict(wf.dag_snapshot)
        node = dag.get_node("task-1")

        scheduler = WorkflowScheduler(session=db_session, redis=redis_client)
        with _patched_reflection("retry_task", confidence=0.2):
            outcome = await scheduler._reflect_on_task(
                workflow=wf, dag=dag, node=node, task_rec=task, response_content="still weak"
            )

        assert outcome is None
        assert task.retry_count == 5  # not incremented — retry was refused
        assert task.status == TaskStatus.RUNNING


class TestReplanBranch:
    @pytest.mark.asyncio
    async def test_replan_injects_alternative_node(self, db_session, redis_client, workflow_and_task):
        wf, task = workflow_and_task
        dag = DAG.from_dict(wf.dag_snapshot)
        node = dag.get_node("task-1")

        scheduler = WorkflowScheduler(session=db_session, redis=redis_client)
        with _patched_reflection("re_plan", confidence=0.4, issues=["wrong approach"]):
            outcome = await scheduler._reflect_on_task(
                workflow=wf, dag=dag, node=node, task_rec=task, response_content="off-topic"
            )

        assert outcome is None
        alt = dag.get_node("task-1_alt")
        assert alt is not None
        assert alt.capability == node.capability
        assert task.output["replan"]["added_node_ids"] == ["task-1_alt"]

    @pytest.mark.asyncio
    async def test_add_verifier_also_injects_alternative(self, db_session, redis_client, workflow_and_task):
        wf, task = workflow_and_task
        dag = DAG.from_dict(wf.dag_snapshot)
        node = dag.get_node("task-1")

        scheduler = WorkflowScheduler(session=db_session, redis=redis_client)
        with _patched_reflection("add_verifier", confidence=0.6, issues=["double-check"]):
            await scheduler._reflect_on_task(
                workflow=wf, dag=dag, node=node, task_rec=task, response_content="needs verification"
            )

        assert dag.get_node("task-1_alt") is not None
        assert task.output["replan"]["action"] == "add_verifier"


class TestAbortBranch:
    @pytest.mark.asyncio
    async def test_abort_saves_signed_checkpoint(
        self, db_session, redis_client, workflow_and_task, instance_keypair_patched
    ):
        wf, task = workflow_and_task
        dag = DAG.from_dict(wf.dag_snapshot)
        node = dag.get_node("task-1")

        scheduler = WorkflowScheduler(session=db_session, redis=redis_client)
        with _patched_reflection("abort", confidence=0.1, issues=["goal impossible", "data missing"]):
            outcome = await scheduler._reflect_on_task(
                workflow=wf, dag=dag, node=node, task_rec=task, response_content="cannot proceed"
            )

        assert outcome is None
        # Task carries the checkpoint hash so operators can look it up.
        hash_ = task.output["abort_checkpoint"]["hash"]
        assert hash_ and isinstance(hash_, str)
        # The persisted memory record must be a real checkpoint.
        store = CheckpointStore(session=db_session, keypair=instance_keypair_patched)
        restored = await store.load_by_hash(hash_)
        assert restored is not None
        assert restored.workflow_id == wf.workflow_id
        assert restored.reason.startswith("reflection_abort")
        # Double-check the raw row carries the expected metadata marker.
        from app.models.memory import MemoryRecord

        record = (await db_session.execute(select(MemoryRecord).where(MemoryRecord.content_hash == hash_))).scalar_one()
        assert is_checkpoint_record(record) is True
