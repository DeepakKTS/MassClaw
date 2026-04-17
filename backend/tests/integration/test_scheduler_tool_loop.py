"""Integration tests for the four scheduler fixes landed on Day 22.

Covers:
  - Fix A: tool-loop exhaustion forces a ``tools=None`` summary call.
  - Fix A (fallback): summary call returns empty → task marked FAILED.
  - Fix B: outer-loop re-entry reuses the Task row for a DAG node.
  - Fix C: ``reserve_budget`` carries the per-node idempotency key.
  - Fix D: low-confidence review fires at most once per task attempt.

Each test mocks only the smallest collaborator needed to reproduce the
pathological path so the scheduler wiring itself is under test.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.intelligence.reflection import ReflectionResult
from app.llm.base import LLMResponse, ToolCall
from app.models.agent import Agent
from app.models.base import AgentStatus, TaskStatus, WalletActionType, WorkflowStatus
from app.models.task import Task
from app.models.wallet import WalletEvent
from app.models.workflow import Workflow
from app.orchestration.dag import DAG, DAGNode
from app.orchestration.scheduler import WorkflowScheduler


def _llm_response(content: str, *, tool_calls: list[ToolCall] | None = None) -> LLMResponse:
    return LLMResponse(
        content=content,
        input_tokens=100,
        output_tokens=50,
        model="claude-sonnet-4-20250514",
        latency_ms=10.0,
        tool_calls=tool_calls,
        raw_response=None,
        metadata={"stop_reason": "tool_use" if tool_calls else "end_turn"},
    )


@pytest_asyncio.fixture
async def workflow_and_node(db_session):
    """Make a workflow + DAG + Task skeleton that tool-loop tests share."""
    agent = Agent(
        name=f"test-agent-{uuid.uuid4().hex[:6]}",
        description="tool-use test agent",
        capabilities=["research"],
        endpoint="internal://test",
        trust_score=0.8,
        status=AgentStatus.ACTIVE,
        cost_profile={"avg_cost_per_call": 0.01},
        latency_profile={"p50_ms": 500, "p95_ms": 2000},
    )
    db_session.add(agent)
    await db_session.flush()

    dag = DAG(
        [
            DAGNode(
                node_id="task-1",
                capability="research",
                description="investigate options",
                depends_on=[],
                status=TaskStatus.RUNNING,
            )
        ]
    )
    wf = Workflow(
        user_id="demo",
        prompt="top 10 agentic AI tools and their uses in the current market trend",
        budget_limit=500.0,
        status=WorkflowStatus.RUNNING,
        dag_snapshot=dag.to_dict(),
    )
    db_session.add(wf)
    await db_session.flush()

    task = Task(
        workflow_id=wf.workflow_id,
        assigned_agent_id=agent.agent_id,
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
    dag.get_node("task-1").task_id = task.task_id
    return wf, dag, task, agent


# --------------------------------------------------------------------- Fix A


class TestToolLoopExhaustion:
    @pytest.mark.asyncio
    async def test_exhaustion_forces_final_summary_call(self, db_session, redis_client, workflow_and_node):
        """After max_iterations of tool_use, a tools=None call produces the final answer."""
        wf, dag, task, agent = workflow_and_node
        scheduler = WorkflowScheduler(session=db_session, redis=redis_client)

        # Patch _store_in_cache out so the test doesn't touch the cache table.
        async def _noop_cache(*a, **k):
            return None

        scheduler._store_in_cache = _noop_cache  # type: ignore[assignment]

        # 5× tool_use responses, then a forced-summary response.
        tool_resp = _llm_response(
            content="I need another tool.",
            tool_calls=[ToolCall(id="t1", name="web_search", arguments={"q": "agentic ai"})],
        )
        final_summary = _llm_response(content="Here is the synthesised list of 10 agentic AI tools...")

        calls: list[dict] = []

        async def _generate(**kwargs):
            calls.append(kwargs)
            # First 5 calls return tool_use, the 6th (tools=None summary) returns text.
            return tool_resp if kwargs.get("tools") else final_summary

        async def _noop_tool_execute(*a, **k):
            class _R:
                success = True
                content = "search result"
                cost_credits = 0.0

            return _R()

        # Patch ModelRouter.generate + ToolExecutor.execute_tool.
        scheduler.model_router.generate = AsyncMock(side_effect=_generate)  # type: ignore[assignment]

        with patch(
            "app.tools.executor.ToolExecutor.execute_tool",
            new=AsyncMock(side_effect=_noop_tool_execute),
        ):
            resp = await scheduler._run_tool_loop(
                node=dag.get_node("task-1"),
                agent=agent,
                workflow=wf,
                user_prompt="find agentic AI tools",
                system_prompt="you are a research agent",
                model="claude-sonnet-4-20250514",
                max_tok=2048,
            )

        assert resp.content.startswith("Here is the synthesised list"), "must use the forced-summary response"
        assert resp.metadata.get("summary_forced") is True
        assert resp.metadata.get("max_iterations_reached") is True
        # The final call MUST have passed tools=None.
        assert calls[-1].get("tools") is None
        # Five tool-use iterations + one summary = six total.
        assert len(calls) == 6

    @pytest.mark.asyncio
    async def test_exhaustion_with_empty_summary_yields_failed_node(self, db_session, redis_client, workflow_and_node):
        """If the forced summary returns empty text, response.content is '' so the
        caller downstream marks the node FAILED — never COMPLETED with junk."""
        wf, dag, task, agent = workflow_and_node
        scheduler = WorkflowScheduler(session=db_session, redis=redis_client)

        async def _noop_cache(*a, **k):
            return None

        scheduler._store_in_cache = _noop_cache  # type: ignore[assignment]

        tool_resp = _llm_response(
            content="",
            tool_calls=[ToolCall(id="t1", name="web_search", arguments={"q": "x"})],
        )
        empty_summary = _llm_response(content="   ")  # whitespace-only counts as empty

        async def _generate(**kwargs):
            return tool_resp if kwargs.get("tools") else empty_summary

        async def _noop_tool_execute(*a, **k):
            class _R:
                success = True
                content = ""
                cost_credits = 0.0

            return _R()

        scheduler.model_router.generate = AsyncMock(side_effect=_generate)  # type: ignore[assignment]

        with patch(
            "app.tools.executor.ToolExecutor.execute_tool",
            new=AsyncMock(side_effect=_noop_tool_execute),
        ):
            resp = await scheduler._run_tool_loop(
                node=dag.get_node("task-1"),
                agent=agent,
                workflow=wf,
                user_prompt="p",
                system_prompt="s",
                model="claude-sonnet-4-20250514",
                max_tok=1024,
            )

        assert resp.content == "", "empty summary should surface as empty content — downstream marks FAILED"
        assert resp.metadata.get("summary_empty") is True
        assert resp.metadata.get("summary_forced") is True


# --------------------------------------------------------------------- Fix B


class TestTaskRowUpsert:
    @pytest.mark.asyncio
    async def test_upsert_reuses_existing_task_row(self, db_session, redis_client, workflow_and_node):
        """Calling _upsert_task_row twice for the same node results in ONE row."""
        wf, dag, task, agent = workflow_and_node
        node = dag.get_node("task-1")
        scheduler = WorkflowScheduler(session=db_session, redis=redis_client)

        # First upsert — node.task_id is already set by the fixture.
        t1 = await scheduler._upsert_task_row(
            node=node,
            workflow_id=wf.workflow_id,
            agent_id=agent.agent_id,
            step_number=1,
            status=TaskStatus.RUNNING,
            error_message=None,
        )
        # Second upsert — simulate the scheduler re-entering the same node
        # after a retry_task reflection branch.
        t2 = await scheduler._upsert_task_row(
            node=node,
            workflow_id=wf.workflow_id,
            agent_id=agent.agent_id,
            step_number=1,
            status=TaskStatus.RUNNING,
            error_message=None,
        )

        assert t1.task_id == t2.task_id == task.task_id

        rows = (await db_session.execute(select(Task).where(Task.workflow_id == wf.workflow_id))).scalars().all()
        assert len(rows) == 1, "outer-loop re-entry must not stamp duplicate Task rows"

    @pytest.mark.asyncio
    async def test_upsert_creates_row_when_task_id_missing(self, db_session, redis_client, workflow_and_node):
        """If node.task_id is None, a fresh row is stamped + wired onto the DAG."""
        wf, dag, task, agent = workflow_and_node
        # Simulate a fresh node that hasn't been executed yet.
        fresh_node = DAGNode(
            node_id="task-2",
            capability="research",
            description="follow-up",
            depends_on=[],
            status=TaskStatus.PENDING,
        )
        scheduler = WorkflowScheduler(session=db_session, redis=redis_client)
        created = await scheduler._upsert_task_row(
            node=fresh_node,
            workflow_id=wf.workflow_id,
            agent_id=agent.agent_id,
            step_number=2,
            status=TaskStatus.RUNNING,
            error_message=None,
        )
        assert created.task_id is not None
        assert fresh_node.task_id == created.task_id
        assert created.status == TaskStatus.RUNNING


# --------------------------------------------------------------------- Fix C


class TestReserveIdempotency:
    @pytest.mark.asyncio
    async def test_reserve_budget_is_idempotent_on_retry(self, db_session, redis_client, workflow_and_node):
        """Two reserve_budget calls with the same idempotency_key produce ONE RESERVE event."""
        from app.services.wallet_service import WalletService

        wf, dag, task, agent = workflow_and_node
        wallet = WalletService(session=db_session, redis=redis_client)
        key = f"{wf.workflow_id}:task-1:reserve"

        r1 = await wallet.reserve_budget(
            workflow_id=wf.workflow_id,
            agent_id=agent.agent_id,
            estimated_cost=12.0,
            reason="first reserve",
            idempotency_key=key,
        )
        r2 = await wallet.reserve_budget(
            workflow_id=wf.workflow_id,
            agent_id=agent.agent_id,
            estimated_cost=12.0,
            reason="second reserve (should dedupe)",
            idempotency_key=key,
        )
        assert r1.wallet_event_id == r2.wallet_event_id, "same key must return same event"

        reserves = (
            (
                await db_session.execute(
                    select(WalletEvent).where(
                        WalletEvent.workflow_id == wf.workflow_id,
                        WalletEvent.action_type == WalletActionType.RESERVE,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(reserves) == 1, "duplicate RESERVE rows leak budget on retry"


# --------------------------------------------------------------------- Fix D


class TestLowConfidenceReviewIdempotency:
    @pytest.mark.asyncio
    async def test_retry_branch_clears_review_flag(self, db_session, redis_client, workflow_and_node):
        """After _reflect_on_task retries a task, review_requested is cleared so the
        next low-confidence output on the retry can legitimately re-trigger review."""
        wf, dag, task, agent = workflow_and_node

        # Pretend the previous attempt already fired a review.
        task.output = {"review_requested": True, "reflection": {"action": "retry_task", "confidence": 0.2}}
        task.confidence = 0.2
        flag_modified(task, "output")
        await db_session.flush()

        scheduler = WorkflowScheduler(session=db_session, redis=redis_client)
        node = dag.get_node("task-1")

        reflection = ReflectionResult(
            should_continue=False,
            confidence=0.2,
            action="retry_task",
            issues=["too short"],
            suggestions=["expand"],
        )
        with patch(
            "app.intelligence.reflection.ReflectionEngine.reflect",
            new=AsyncMock(return_value=reflection),
        ):
            outcome = await scheduler._reflect_on_task(
                workflow=wf,
                dag=dag,
                node=node,
                task_rec=task,
                response_content="weak attempt",
            )
        # Retry branch signals continue_loop so the outer scheduler re-enters
        # and the review flag must be cleared ready for the next attempt.
        assert outcome is not None and outcome.get("continue_loop") is True
        assert "review_requested" not in task.output, (
            "retry branch must clear review_requested so the next attempt's "
            "low-confidence output can re-trigger the review gate"
        )
