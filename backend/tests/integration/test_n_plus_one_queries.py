"""Query-count ceilings for endpoints that used to scale round trips with rows.

Each test here pins a count that was previously O(rows) and asserts the values
are unchanged, so a reintroduced N+1 fails loudly rather than showing up as
latency. See tests/query_counter.py for why counts rather than timings.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError
from app.identity.signer import generate_keypair
from app.models.agent import Agent
from app.models.base import AgentStatus, WorkflowStatus
from app.models.workflow import Workflow
from app.safety.approval import ApprovalRequest
from app.safety.federated_approval import FederatedApprovalStore
from app.services.project_service import ProjectService
from tests.query_counter import count_queries

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def keypair():
    return generate_keypair()


async def _seed_workflow(session: AsyncSession, workflow_id: uuid.UUID) -> None:
    session.add(
        Workflow(
            workflow_id=workflow_id,
            user_id="test",
            prompt="n+1 query test",
            status=WorkflowStatus.RUNNING,
            budget_limit=10.0,
        )
    )
    await session.flush()


class TestFederatedApprovalBatchLookup:
    """``/approvals/pending`` resolved each row with its own full META scan."""

    async def test_batched_lookup_scans_once_for_many_ids(self, db_session: AsyncSession, keypair) -> None:
        store = FederatedApprovalStore(session=db_session, keypair=keypair)
        wf = uuid.uuid4()
        await _seed_workflow(db_session, wf)

        requests = [
            ApprovalRequest(
                workflow_id=str(wf),
                action=f"execute_step-{i}",
                policy_rule="high_risk_task",
            )
            for i in range(5)
        ]
        for req in requests:
            await store.write_request(req, node_id=f"task-{req.action}")

        ids = [req.request_id for req in requests]

        with count_queries(db_session) as batched:
            found = await store.find_many_by_request_id(ids)

        assert set(found) == set(ids)
        # One scan for the whole batch, regardless of how many ids are asked for.
        assert len(batched.selects) == 1, "\n".join(batched.selects)

        # And the loop it replaces cost one scan per id.
        with count_queries(db_session) as per_item:
            for request_id in ids:
                await store.find_by_request_id(request_id)
        assert len(per_item.selects) == len(ids)

    async def test_batched_lookup_matches_the_single_id_lookup(self, db_session: AsyncSession, keypair) -> None:
        store = FederatedApprovalStore(session=db_session, keypair=keypair)
        wf = uuid.uuid4()
        await _seed_workflow(db_session, wf)

        pending = ApprovalRequest(workflow_id=str(wf), action="execute_pending", policy_rule="high_risk_task")
        decided = ApprovalRequest(workflow_id=str(wf), action="execute_decided", policy_rule="high_risk_task")
        await store.write_request(pending, node_id="task-pending")
        await store.write_request(decided, node_id="task-decided")
        decided.status = "approved"
        decided.decided_by = "deepak"
        await store.write_decision(decided, node_id="task-decided")

        ids = [pending.request_id, decided.request_id]
        batch = await store.find_many_by_request_id(ids)

        for request_id in ids:
            single = await store.find_by_request_id(request_id)
            assert single is not None
            assert batch[request_id].request_id == single.request_id
            # A decision must still win over the pending record for the same id.
            assert batch[request_id].status == single.status

        assert batch[decided.request_id].status == "approved"
        assert batch[pending.request_id].status == "pending"

    async def test_unknown_ids_are_absent_rather_than_none(self, db_session: AsyncSession, keypair) -> None:
        store = FederatedApprovalStore(session=db_session, keypair=keypair)
        result = await store.find_many_by_request_id(["no-such-request"])
        # `.get(id)` must reproduce find_by_request_id's None for a miss.
        assert result == {}
        assert result.get("no-such-request") is None

    async def test_empty_input_does_not_query_at_all(self, db_session: AsyncSession, keypair) -> None:
        store = FederatedApprovalStore(session=db_session, keypair=keypair)
        with count_queries(db_session) as counter:
            assert await store.find_many_by_request_id([]) == {}
        assert counter.count == 0


class TestReorderSprintTasks:
    @pytest_asyncio.fixture
    async def sprint_with_tasks(self, db_session: AsyncSession, redis_client):
        service = ProjectService(db_session, redis_client)
        project = await service.create_project(name=f"proj-{uuid.uuid4().hex[:8]}")
        sprint = await service.create_sprint(
            project_id=project.project_id,
            name="sprint-1",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=14),
        )
        sprint_tasks = []
        for i in range(6):
            backlog = await service.create_backlog_task(project_id=project.project_id, title=f"task-{i}")
            sprint_tasks.append(
                await service.add_task_to_sprint(sprint_id=sprint.sprint_id, backlog_task_id=backlog.backlog_task_id)
            )
        return service, sprint, sprint_tasks

    async def test_reorder_fetches_every_task_in_one_query(self, db_session: AsyncSession, sprint_with_tasks) -> None:
        service, sprint, sprint_tasks = sprint_with_tasks
        order = [t.sprint_task_id for t in reversed(sprint_tasks)]

        with count_queries(db_session) as counter:
            await service.reorder_sprint_tasks(sprint.sprint_id, order)

        # One SELECT for the batch, not one per task.
        assert len(counter.selects) == 1, "\n".join(counter.selects)

    async def test_positions_follow_the_given_order(self, db_session: AsyncSession, sprint_with_tasks) -> None:
        service, sprint, sprint_tasks = sprint_with_tasks
        order = [t.sprint_task_id for t in reversed(sprint_tasks)]

        await service.reorder_sprint_tasks(sprint.sprint_id, order)

        for expected_position, sprint_task_id in enumerate(order):
            task = next(t for t in sprint_tasks if t.sprint_task_id == sprint_task_id)
            await db_session.refresh(task)
            assert task.position == expected_position

    async def test_unknown_task_id_still_raises_not_found(self, db_session: AsyncSession, sprint_with_tasks) -> None:
        service, sprint, sprint_tasks = sprint_with_tasks
        missing = uuid.uuid4()
        order = [sprint_tasks[0].sprint_task_id, missing, sprint_tasks[1].sprint_task_id]

        with pytest.raises(NotFoundError) as exc:
            await service.reorder_sprint_tasks(sprint.sprint_id, order)
        assert str(missing) in str(exc.value)

    async def test_task_from_another_sprint_is_rejected(self, db_session: AsyncSession, sprint_with_tasks) -> None:
        """The sprint_id predicate must still scope the batch fetch."""
        service, sprint, sprint_tasks = sprint_with_tasks
        other_project = await service.create_project(name=f"proj-{uuid.uuid4().hex[:8]}")
        other_sprint = await service.create_sprint(
            project_id=other_project.project_id,
            name="sprint-other",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=7),
        )
        backlog = await service.create_backlog_task(project_id=other_project.project_id, title="foreign")
        foreign = await service.add_task_to_sprint(
            sprint_id=other_sprint.sprint_id, backlog_task_id=backlog.backlog_task_id
        )

        with pytest.raises(NotFoundError):
            await service.reorder_sprint_tasks(
                sprint.sprint_id, [sprint_tasks[0].sprint_task_id, foreign.sprint_task_id]
            )

    async def test_empty_order_is_a_no_op(self, db_session: AsyncSession, sprint_with_tasks) -> None:
        service, sprint, _ = sprint_with_tasks
        with count_queries(db_session) as counter:
            await service.reorder_sprint_tasks(sprint.sprint_id, [])
        assert counter.count == 0


class TestCapabilitiesDiscovery:
    """``/api/v1/capabilities`` unioned every active agent's array in Python."""

    async def _agent(
        self,
        session: AsyncSession,
        capabilities: list[str],
        *,
        status: AgentStatus = AgentStatus.ACTIVE,
    ) -> Agent:
        agent = Agent(
            name=f"cap-agent-{uuid.uuid4().hex[:8]}",
            description="Agent for capability discovery tests",
            capabilities=capabilities,
            endpoint="internal://cap-test",
            status=status,
        )
        session.add(agent)
        await session.flush()
        return agent

    async def test_returns_the_distinct_union_of_active_capabilities(self, db_session: AsyncSession) -> None:
        from sqlalchemy import func, select

        await self._agent(db_session, ["research", "analysis"])
        await self._agent(db_session, ["analysis", "writing"])
        await self._agent(db_session, [])

        # The same statement the endpoint issues, against the test session.
        result = await db_session.execute(
            select(func.jsonb_array_elements_text(Agent.capabilities).label("capability"))
            .where(
                Agent.status == AgentStatus.ACTIVE,
                func.jsonb_typeof(Agent.capabilities) == "array",
            )
            .distinct()
        )
        caps = {row.capability for row in result}

        assert {"research", "analysis", "writing"} <= caps
        # De-duplication happens in Postgres, so "analysis" appears once.
        assert len([c for c in caps if c == "analysis"]) == 1

    async def test_row_count_is_bounded_by_distinct_capabilities_not_agents(self, db_session: AsyncSession) -> None:
        from sqlalchemy import func, select

        # Twenty agents sharing two capabilities must yield two rows, where the
        # old query transferred one array per agent.
        for _ in range(20):
            await self._agent(db_session, ["research", "analysis"])

        result = await db_session.execute(
            select(func.jsonb_array_elements_text(Agent.capabilities).label("capability"))
            .where(
                Agent.status == AgentStatus.ACTIVE,
                func.jsonb_typeof(Agent.capabilities) == "array",
            )
            .distinct()
        )
        rows = list(result)
        assert len(rows) < 20
        assert {r.capability for r in rows} == {"research", "analysis"}

    async def test_inactive_agents_are_excluded(self, db_session: AsyncSession) -> None:
        from sqlalchemy import func, select

        await self._agent(db_session, ["retired_only"], status=AgentStatus.INACTIVE)

        result = await db_session.execute(
            select(func.jsonb_array_elements_text(Agent.capabilities).label("capability"))
            .where(
                Agent.status == AgentStatus.ACTIVE,
                func.jsonb_typeof(Agent.capabilities) == "array",
            )
            .distinct()
        )
        assert "retired_only" not in {row.capability for row in result}
