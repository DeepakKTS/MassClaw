from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.events import EventBus
from app.core.logging import get_logger
from app.exceptions import BudgetExhaustedError
from app.llm.base import LLMResponse
from app.llm.router import get_model_router
from app.llm.token_counter import tokens_to_credits
from app.models.agent import Agent
from app.models.base import MemoryType, TaskStatus, WorkflowStatus
from app.models.task import Task
from app.models.workflow import Workflow
from app.orchestration.dag import DAG, DAGNode
from app.orchestration.selector import AgentSelector
from app.orchestration.synthesizer import OutputSynthesizer
from app.schemas.memory import MemoryWriteRequest
from app.schemas.trust import TrustScoreInput
from app.services.memory_service import MemoryService
from app.services.trust_service import TrustService
from app.services.wallet_service import WalletService

logger = get_logger(__name__)

# Agent system prompts by capability
AGENT_PROMPTS: dict[str, str] = {
    "intake": (
        "You are an intake specialist. Parse the user's request and extract: "
        "(1) the specific operational area, (2) key constraints, "
        "(3) desired outcomes, (4) stakeholders involved. "
        "Output structured, clear findings."
    ),
    "classify": (
        "You are a classification specialist. Categorize the request into domain, "
        "urgency, complexity, and required expertise areas."
    ),
    "extract-requirements": (
        "You are a requirements extraction specialist. Identify all explicit and implicit "
        "requirements from the request and list them clearly."
    ),
    "research": (
        "You are a research specialist. Given the context, gather and synthesize "
        "background information, industry benchmarks, best practices, and relevant data."
    ),
    "data-retrieval": (
        "You are a data retrieval specialist. Find and compile relevant data points, "
        "statistics, and metrics related to the topic."
    ),
    "literature-review": (
        "You are a literature review specialist. Survey existing knowledge, studies, "
        "and publications relevant to the topic and summarize key findings."
    ),
    "process-analysis": (
        "You are a process analysis specialist. Analyze workflows, identify bottlenecks, "
        "inefficiencies, and areas for improvement."
    ),
    "workflow-mapping": (
        "You are a workflow mapping specialist. Map out the current process flow, "
        "identify dependencies, and highlight critical paths."
    ),
    "bottleneck-detection": (
        "You are a bottleneck detection specialist. Identify resource constraints, "
        "throughput limits, and queue buildup points in the process."
    ),
    "risk-assessment": (
        "You are a risk assessment specialist. Identify regulatory, safety, compliance, "
        "operational, and financial risks. Rate each by severity and likelihood."
    ),
    "compliance-check": (
        "You are a compliance specialist. Check for regulatory and legal compliance gaps "
        "and recommend remediation actions."
    ),
    "safety-analysis": (
        "You are a safety analysis specialist. Evaluate potential safety hazards and recommend preventive measures."
    ),
    "optimization": (
        "You are an optimization specialist. Propose concrete improvements for efficiency, "
        "resource allocation, and scheduling based on the analysis."
    ),
    "scheduling": (
        "You are a scheduling specialist. Design optimal schedules considering constraints, "
        "demand patterns, and resource availability."
    ),
    "resource-allocation": (
        "You are a resource allocation specialist. Determine optimal distribution of "
        "personnel, equipment, and budget across operations."
    ),
    "cost-analysis": (
        "You are a financial analysis specialist. Estimate costs, project ROI, "
        "and evaluate budget implications of proposed changes."
    ),
    "budget-estimation": (
        "You are a budget estimation specialist. Create detailed cost projections for proposed initiatives."
    ),
    "roi-projection": (
        "You are an ROI specialist. Calculate return on investment for proposed changes with confidence intervals."
    ),
    "summarization": (
        "You are a synthesis specialist. Integrate all findings into a clear, "
        "actionable executive summary with key recommendations."
    ),
    "report-generation": (
        "You are a report generation specialist. Create a comprehensive, well-structured "
        "report from the collected findings."
    ),
    "executive-brief": (
        "You are an executive briefing specialist. Create a concise, high-impact brief for senior leadership."
    ),
    "verification": (
        "You are a quality verification specialist. Cross-check all outputs for "
        "logical consistency, factual accuracy, completeness, and potential contradictions. "
        "Flag any issues found."
    ),
    "quality-check": (
        "You are a quality assurance specialist. Review outputs for completeness, "
        "accuracy, and adherence to requirements."
    ),
    "consistency-audit": (
        "You are a consistency auditor. Check that all outputs are internally consistent "
        "and that conclusions follow from the evidence."
    ),
    "data-analytics": (
        "You are a data analytics specialist. Analyze datasets, compute statistics, "
        "identify trends, and produce insights."
    ),
    "visualization": (
        "You are a visualization specialist. Design effective data visualizations "
        "and describe charts that communicate findings clearly."
    ),
}

DEFAULT_AGENT_PROMPT = (
    "You are a specialized AI agent. Complete the assigned task thoroughly and accurately. "
    "Build on the provided context from prior agents. Be specific and actionable."
)


class WorkflowScheduler:
    """The core orchestration engine.

    Executes a workflow DAG with:
    - Dependency-aware parallel execution via asyncio.gather
    - Shared memory piping between agent steps
    - Wallet budget tracking per task
    - Trust score updates after each task
    - Retry with fallback agent on failure
    - Real-time progress events via Redis pub/sub
    """

    MAX_RETRIES = 2

    def __init__(
        self,
        session: AsyncSession,
        redis: aioredis.Redis,
    ) -> None:
        self.session = session
        self.redis = redis
        self.selector = AgentSelector(session)
        self.synthesizer = OutputSynthesizer()
        self.memory_service = MemoryService(session, redis)
        self.wallet_service = WalletService(session, redis)
        self.trust_service = TrustService(session, redis)
        self.model_router = get_model_router()

    async def execute_workflow(
        self,
        workflow: Workflow,
        dag: DAG,
        agents: dict[str, Agent],
    ) -> dict:
        """Execute a complete workflow DAG.

        This is the main orchestration loop:
        1. While incomplete: find ready nodes, execute in parallel
        2. Each task: build context -> LLM call -> write memory -> update trust
        3. On failure: retry with fallback agent, then skip dependents
        4. Finally: synthesize outputs into final result
        """
        workflow.status = WorkflowStatus.RUNNING
        workflow.started_at = datetime.now(UTC)
        workflow.dag_snapshot = dag.to_dict()
        # Flush (not commit) — the scheduler loop below relies on the
        # workflow ORM object staying live for the entire execution.
        # session.commit() here expires the object and causes the loop
        # to silently stall between task batches when the approval-gate
        # code re-reads workflow attributes. The RUNNING transition is
        # still visible to readers via a short-lived SELECT on the
        # tasks table (they count task rows) + the DAG snapshot write
        # that strategy_router already committed before handing off.
        await self.session.flush()

        await self._publish_progress(workflow, dag, "workflow_started")

        # Execution loop
        while not dag.is_complete:
            ready_nodes = dag.get_ready_nodes()
            if not ready_nodes:
                break

            # Phase 1: Prepare all ready nodes — create Task records, reserve budget, build context
            node_contexts: dict[str, tuple[Task, Agent, str]] = {}
            reservations: dict[str, any] = {}  # node_id -> wallet reservation event
            for node in ready_nodes:
                agent = agents.get(node.node_id)
                if agent is None:
                    dag.mark_failed(node.node_id, error="No agent assigned")
                    continue

                # Pre-execution budget check — fail fast before wasting LLM calls.
                # idempotency_key scoped to (workflow, node) so outer-loop
                # re-entries after a retry don't burn budget on duplicate
                # RESERVE events (Fix C — Day 22).
                estimated_cost = self._estimate_node_cost(agent)
                reserve_key = f"{workflow.workflow_id}:{node.node_id}:reserve"
                try:
                    reservation = await self.wallet_service.reserve_budget(
                        workflow_id=workflow.workflow_id,
                        agent_id=agent.agent_id,
                        estimated_cost=estimated_cost,
                        reason=f"Task {node.node_id}: {node.capability}",
                        idempotency_key=reserve_key,
                    )
                    reservations[node.node_id] = reservation
                except BudgetExhaustedError:
                    dag.mark_failed(
                        node.node_id,
                        error=f"Budget insufficient for {node.capability}: need ~{estimated_cost:.1f} cr",
                    )
                    # Fix B — Day 22: upsert the task row by node.task_id so
                    # budget-exhausted retries don't stamp duplicate FAILED
                    # rows per (workflow_id, node_id).
                    step_number = int("".join(c for c in node.node_id if c.isdigit()) or "0")
                    failed_task = await self._upsert_task_row(
                        node=node,
                        workflow_id=workflow.workflow_id,
                        agent_id=agent.agent_id,
                        step_number=step_number,
                        status=TaskStatus.FAILED,
                        error_message=f"Budget insufficient: need ~{estimated_cost:.1f} cr",
                    )
                    logger.warning(
                        "task_budget_insufficient",
                        node_id=node.node_id,
                        capability=node.capability,
                        estimated_cost=estimated_cost,
                        task_id=str(failed_task.task_id),
                    )
                    continue

                dag.mark_running(node.node_id)

                # Fix B — Day 22: re-use the existing Task row whenever the
                # outer scheduler loop re-enters this node (e.g. after a
                # retry_task reflection branch called dag.mark_pending).
                # Without this, every tick of the while-not-is_complete loop
                # creates a fresh Task row, producing 40+ duplicates for a
                # 5-node DAG when the tool-loop can't converge.
                step_number = int("".join(c for c in node.node_id if c.isdigit()) or "0")
                task = await self._upsert_task_row(
                    node=node,
                    workflow_id=workflow.workflow_id,
                    agent_id=agent.agent_id,
                    step_number=step_number,
                    status=TaskStatus.RUNNING,
                    error_message=None,
                )

                # Build context from shared memory (DB read)
                context = await self._build_context(workflow, node, dag)
                node_contexts[node.node_id] = (task, agent, context)

                await self._publish_progress(workflow, dag, "task_started", node=node, agent=agent)

            # Phase 1.5: Approval gate for high-risk capabilities
            APPROVAL_REQUIRED_CAPABILITIES = {"risk-assessment", "compliance-check", "safety-analysis"}
            active_nodes = [n for n in ready_nodes if n.node_id in node_contexts]
            nodes_needing_approval = [n for n in active_nodes if n.capability in APPROVAL_REQUIRED_CAPABILITIES]

            # Policy engine evaluation runs first. A DENY removes the
            # node from the active set; an ESCALATE_HUMAN routes it
            # through the same approval gate as the hardcoded
            # high-risk capabilities. Until Day-16 rules land, the
            # registry is empty and every call aggregates to ABSTAIN,
            # i.e. this block is a no-op at runtime.
            policy_escalated_nodes = await self._policy_evaluate_nodes(
                workflow=workflow, active_nodes=active_nodes, node_contexts=node_contexts
            )
            for node in policy_escalated_nodes:
                if node not in nodes_needing_approval:
                    nodes_needing_approval.append(node)

            if nodes_needing_approval:
                from app.core.redis import get_redis_manager
                from app.orchestration.checkpoint import CheckpointStore, WorkflowCheckpoint
                from app.safety.approval import ApprovalManager
                from app.services.identity_service import get_instance_key_store

                approval_mgr = ApprovalManager(get_redis_manager().get_cache_client())

                for node in nodes_needing_approval:
                    task_rec = node_contexts[node.node_id][0]
                    agent = node_contexts[node.node_id][1]

                    # Persist a signed checkpoint BEFORE we block on the
                    # human. Must use a SEPARATE autocommit session so the
                    # record is durable + visible to the /resume endpoint
                    # (which runs in its own session) before we go to sleep
                    # waiting for the human. Using self.session here would
                    # leave the checkpoint in an uncommitted transaction
                    # for the duration of the approval wait — any resume
                    # attempt from another connection would 404.
                    #
                    # The content hash propagates via CRDT gossip once
                    # committed, so any federated peer can also pull the
                    # state and resume execution once the decision lands.
                    checkpoint_hash: str | None = None
                    try:
                        keypair = get_instance_key_store().instance_keypair()
                        from app.core.database import db_session_context as _cp_session_ctx

                        async with _cp_session_ctx() as cp_session:
                            cp_store = CheckpointStore(session=cp_session, keypair=keypair)
                            cp = WorkflowCheckpoint.from_dag(
                                workflow_id=workflow.workflow_id,
                                dag=dag,
                                reason=f"awaiting_approval: {node.capability}",
                                current_task_id=node.node_id,
                                variables={
                                    "capability": node.capability,
                                    "agent": agent.name,
                                },
                            )
                            saved = await cp_store.save(cp)
                            checkpoint_hash = saved.content_hash
                    except Exception as exc:
                        # Checkpoint failures must not block the approval
                        # flow — fall back to Redis-only behaviour and log.
                        logger.warning(
                            "approval_checkpoint_failed",
                            workflow_id=str(workflow.workflow_id),
                            node_id=node.node_id,
                            error=str(exc),
                        )

                    request = await approval_mgr.request_approval(
                        workflow_id=str(workflow.workflow_id),
                        task_id=str(task_rec.task_id),
                        action=f"execute_{node.capability}",
                        context={
                            "workflow_id": str(workflow.workflow_id),
                            "task_id": str(task_rec.task_id),
                            "capability": node.capability,
                            "description": node.description,
                            "agent": agent.name,
                        },
                        policy_rule="high_risk_task",
                        timeout_seconds=300,
                        checkpoint_hash=checkpoint_hash,
                    )

                    # Non-blocking: check if auto-approved by policy, otherwise mark as awaiting
                    try:
                        decision = await approval_mgr.check_status(request.request_id)
                    except ValueError:
                        decision = None

                    if decision and decision.status == "pending":
                        task_rec.status = TaskStatus.AWAITING_APPROVAL
                        task_rec.error_message = f"Awaiting human approval (request: {request.request_id})"

                        # AWAITING_APPROVAL is the publicly-visible workflow
                        # state while a human reviewer decides. Commit so
                        # the status is visible to the /status endpoint
                        # running in a separate session — otherwise the
                        # stock agent polling status would never see the
                        # state and could not even know it needs to
                        # approve the request.
                        workflow.status = WorkflowStatus.AWAITING_APPROVAL
                        await self.session.commit()

                        await self._publish_progress(
                            workflow,
                            dag,
                            "approval_required",
                            node=node,
                            extra={
                                "approval_id": request.request_id,
                                "capability": node.capability,
                                "checkpoint_hash": checkpoint_hash,
                            },
                        )

                        # Wait for decision (with timeout)
                        final = await approval_mgr.wait_for_decision(request.request_id, timeout=300)

                        workflow.status = WorkflowStatus.RUNNING
                        await self.session.commit()

                        if final and final.status == "denied":
                            dag.mark_failed(node.node_id, error=f"Denied by human: {final.decided_by or 'reviewer'}")
                            task_rec.status = TaskStatus.SKIPPED
                            task_rec.error_message = (
                                f"Denied: {final.context.get('denial_reason', 'No reason provided')}"
                            )
                            await self.session.flush()
                            # Remove from active nodes so it's skipped in Phase 2
                            if node.node_id in node_contexts:
                                del node_contexts[node.node_id]
                            continue

                        # Approved or expired — restore task status and proceed
                        task_rec.status = TaskStatus.RUNNING
                        task_rec.error_message = None
                        await self.session.flush()

            # Phase 2: Run LLM calls in parallel (budget already reserved)
            # Tiered Model Cascade: Haiku for simple tasks, Sonnet for complex/verification
            HAIKU_CAPABILITIES = {
                "intake",
                "classify",
                "extract-requirements",
                "data-retrieval",
                "literature-review",
                "summarization",
            }
            SONNET_CAPABILITIES = {
                "verification",
                "quality-check",
                "consistency-audit",
                "risk-assessment",
                "compliance-check",
                "safety-analysis",
                "optimization",
                "process-analysis",
                "report-generation",
                "executive-brief",
                "cost-analysis",
            }

            async def _llm_call(node: DAGNode) -> tuple[DAGNode, LLMResponse | Exception]:
                task_rec, agent, context = node_contexts[node.node_id]
                system_prompt = AGENT_PROMPTS.get(node.capability, DEFAULT_AGENT_PROMPT)
                user_prompt = self._build_agent_prompt(node, context)

                # Tiered model selection
                if node.capability in HAIKU_CAPABILITIES and node.estimated_complexity != "high":
                    model = "claude-haiku-4-5-20251001"
                    max_tok = 1000
                elif node.capability in SONNET_CAPABILITIES or node.estimated_complexity == "high":
                    model = "claude-sonnet-4-20250514"
                    max_tok = 2000
                else:
                    # Default: Sonnet for unknown capabilities
                    model = "claude-sonnet-4-20250514"
                    max_tok = 1200

                # Strategy 3: Semantic Result Cache — check before calling LLM
                cached_result = await self._check_semantic_cache(node, user_prompt)
                if cached_result:
                    logger.info("cache_hit", capability=node.capability, node_id=node.node_id)
                    return node, cached_result

                try:
                    resp = await self._run_tool_loop(
                        node=node,
                        agent=agent,
                        workflow=workflow,
                        user_prompt=user_prompt,
                        system_prompt=system_prompt,
                        model=model,
                        max_tok=max_tok,
                    )
                except Exception as e:
                    return node, e
                return node, resp

            # Recompute active_nodes to exclude any denied/removed nodes from Phase 1.5
            active_nodes = [n for n in ready_nodes if n.node_id in node_contexts]
            llm_results = await asyncio.gather(*[_llm_call(n) for n in active_nodes])

            # Phase 3: Process results — charge actual cost (reservation already done)
            for node, resp_or_err in llm_results:
                task_rec, agent, context = node_contexts[node.node_id]

                if isinstance(resp_or_err, Exception):
                    task_rec.status = TaskStatus.FAILED
                    task_rec.error_message = str(resp_or_err)
                    await self.session.flush()
                    # Release the reservation since the LLM call failed
                    if node.node_id in reservations:
                        try:
                            await self.wallet_service.release_reservation(
                                workflow_id=workflow.workflow_id,
                                reservation_event_id=reservations[node.node_id].wallet_event_id,
                                reason=f"Task {node.node_id} failed: LLM error",
                            )
                        except Exception:
                            pass  # best-effort release
                    await self._handle_failure(workflow, dag, node, agents, resp_or_err)
                    continue

                response: LLMResponse = resp_or_err
                now = datetime.now(UTC)
                latency_ms = response.latency_ms

                charged = False
                try:
                    # Charge actual cost against the reservation
                    actual_credits = tokens_to_credits(response.cost)
                    reservation = reservations.get(node.node_id)
                    idem_key = f"{workflow.workflow_id}:{node.node_id}:charge"
                    if reservation:
                        await self.wallet_service.charge(
                            workflow_id=workflow.workflow_id,
                            agent_id=agent.agent_id,
                            actual_cost=actual_credits,
                            reservation_event_id=reservation.wallet_event_id,
                            reason=f"Task {node.node_id} completed: {response.total_tokens} tokens",
                            idempotency_key=idem_key,
                        )
                        charged = True

                    # Write to shared memory
                    await self.memory_service.write_memory(
                        MemoryWriteRequest(
                            workflow_id=workflow.workflow_id,
                            source_agent_id=agent.agent_id,
                            memory_type=MemoryType.RESULT,
                            content=response.content,
                            confidence=0.85,
                            metadata={"task_id": str(task_rec.task_id), "capability": node.capability},
                        )
                    )

                    # Update trust
                    quality_score = min(1.0, len(response.content) / 500)
                    latency_score = max(0, 1.0 - min(latency_ms / 30000, 1.0))
                    cost_score = max(0, 1.0 - min(actual_credits / 50, 1.0))
                    await self.trust_service.record_trust_event(
                        agent_id=agent.agent_id,
                        scores=TrustScoreInput(
                            quality_score=quality_score,
                            latency_score=latency_score,
                            cost_score=cost_score,
                            consistency_score=await self.trust_service.compute_consistency_score(agent.agent_id),
                            reliability_score=await self.trust_service.compute_reliability_score(agent.agent_id),
                        ),
                        workflow_id=workflow.workflow_id,
                        task_id=task_rec.task_id,
                    )

                    # Mark completed
                    task_rec.status = TaskStatus.COMPLETED
                    task_rec.output = {"content": response.content, "model": response.model}
                    task_rec.confidence = quality_score
                    task_rec.cost_used = Decimal(str(actual_credits))
                    task_rec.latency_ms = latency_ms
                    task_rec.completed_at = now
                    await self.session.flush()

                    dag.mark_completed(node.node_id, output=response.content)

                    # Consensus verification for high-risk capabilities
                    HIGH_RISK_CAPABILITIES = {"risk-assessment", "compliance-check", "safety-analysis"}
                    if node.capability in HIGH_RISK_CAPABILITIES:
                        try:
                            from app.services.consensus_service import ConsensusService

                            consensus_svc = ConsensusService(session=self.session)
                            consensus = await consensus_svc.verify_with_consensus(
                                output=response.content,
                                task_description=f"{node.capability}: {node.description}",
                            )
                            if not consensus.agreed:
                                task_rec.output["consensus_warning"] = (
                                    f"Consensus not reached (agreement: {consensus.weighted_agreement:.0%}). "
                                    "Output may require manual review."
                                )
                                task_rec.confidence = max(0.3, task_rec.confidence * consensus.weighted_agreement)
                                await self.session.flush()
                                logger.warning(
                                    "consensus_not_reached",
                                    node_id=node.node_id,
                                    agreement=round(consensus.weighted_agreement, 3),
                                )
                        except Exception as e:
                            logger.warning("consensus_verification_skipped", error=str(e))

                    # Post-execution review gate for low-confidence outputs.
                    # Fix D — Day 22: fire at most once per task attempt.
                    # Without this flag, the outer scheduler loop + a
                    # retry_task reflection branch could re-enter this
                    # block on every tick and flood the approvals queue
                    # with thousands of "review_low_confidence_output"
                    # records for a single node. The flag is cleared in
                    # the reflection branch when a retry resets the node
                    # so a fresh low-confidence output can re-trigger.
                    task_output_map = task_rec.output if isinstance(task_rec.output, dict) else {}
                    review_already_requested = bool(task_output_map.get("review_requested"))
                    if task_rec.confidence is not None and task_rec.confidence < 0.4 and not review_already_requested:
                        try:
                            from sqlalchemy.orm.attributes import flag_modified

                            from app.core.redis import get_redis_manager
                            from app.safety.approval import ApprovalManager

                            review_mgr = ApprovalManager(get_redis_manager().get_cache_client())
                            await review_mgr.request_approval(
                                workflow_id=str(workflow.workflow_id),
                                task_id=str(task_rec.task_id),
                                action="review_low_confidence_output",
                                context={
                                    "workflow_id": str(workflow.workflow_id),
                                    "task_id": str(task_rec.task_id),
                                    "capability": node.capability,
                                    "confidence": task_rec.confidence,
                                    "output_preview": response.content[:500],
                                },
                                policy_rule="low_confidence_review",
                                timeout_seconds=120,
                            )
                            task_rec.output = {**task_output_map, "review_requested": True}
                            flag_modified(task_rec, "output")
                            await self.session.flush()
                            logger.info(
                                "low_confidence_review_requested",
                                node_id=node.node_id,
                                confidence=task_rec.confidence,
                            )
                        except Exception as e:
                            logger.warning("review_request_failed", error=str(e))

                    # Adaptive Intelligence: four-branch reflection on task output.
                    #
                    # Dispatches to the existing intelligence modules so the
                    # logic stays in one place per concern:
                    #   - retry_task → SelfCorrectionEngine decides + builds
                    #     an improved prompt; we reset the DAG node and the
                    #     next scheduler tick re-executes with the feedback.
                    #   - re_plan    → DynamicReplanner injects an alternative
                    #     task node into the DAG; the original stays completed
                    #     so the audit trail shows what was tried.
                    #   - add_verifier → DynamicReplanner adds a verifier as
                    #     a re_plan-shaped alternative; enough for Phase 1.
                    #   - abort      → persist a workflow checkpoint (so Day 13
                    #     cross-node resume has something to pick up) and let
                    #     the main loop surface the failure via dag status.
                    #   - accept     → happy path, no branch action.
                    try:
                        reflection_outcome = await self._reflect_on_task(
                            workflow=workflow,
                            dag=dag,
                            node=node,
                            task_rec=task_rec,
                            response_content=response.content,
                        )
                    except Exception as e:  # reflection must never break the run.
                        logger.warning(
                            "reflection_failed",
                            node_id=node.node_id,
                            error=str(e),
                        )
                        reflection_outcome = None

                    if reflection_outcome and reflection_outcome.get("continue_loop"):
                        # The branch (usually retry_task) reset the DAG node
                        # and wants the scheduler to skip downstream
                        # bookkeeping and re-enter the outer loop.
                        continue

                    await self._publish_progress(
                        workflow,
                        dag,
                        "task_completed",
                        node=node,
                        agent=agent,
                        extra={
                            "latency_ms": round(latency_ms, 1),
                            "cost_credits": round(actual_credits, 4),
                            "model_used": response.model,
                            "content_preview": response.content[:300] if response.content else "",
                            "content_length": len(response.content) if response.content else 0,
                        },
                    )
                    logger.info(
                        "task_completed",
                        workflow_id=str(workflow.workflow_id),
                        node_id=node.node_id,
                        capability=node.capability,
                        agent=agent.name,
                        latency_ms=round(latency_ms, 1),
                        tokens=response.total_tokens,
                    )

                except Exception as e:
                    # Release reservation if charge hasn't happened yet
                    if not charged and node.node_id in reservations:
                        try:
                            await self.wallet_service.release_reservation(
                                workflow_id=workflow.workflow_id,
                                reservation_event_id=reservations[node.node_id].wallet_event_id,
                                reason=f"Task {node.node_id} post-processing failed: {e}",
                            )
                        except Exception:
                            logger.warning("reservation_release_failed", node_id=node.node_id)
                    task_rec.status = TaskStatus.FAILED
                    task_rec.error_message = str(e)
                    await self.session.flush()
                    await self._handle_failure(workflow, dag, node, agents, e)

            # Persist DAG state
            workflow.dag_snapshot = dag.to_dict()
            await self.session.flush()

            await self._publish_progress(workflow, dag, "step_batch_completed")

        # Synthesize final output
        synthesis_failed = False
        try:
            final_result = await self.synthesizer.synthesize(dag, workflow.prompt)
        except Exception as e:
            logger.error("synthesis_failed", error=str(e))
            synthesis_failed = True
            final_result = {
                "content": "Synthesis failed. Individual task outputs are available in the DAG.",
                "confidence": 0.0,
                "summary": f"Synthesis error: {e}",
                "synthesis_failed": True,
            }

        # Finalize workflow
        if dag.failed_count > 0 and dag.completed_count == 0:
            workflow.status = WorkflowStatus.FAILED
        elif dag.failed_count > 0 and dag.completed_count > 0:
            workflow.status = WorkflowStatus.COMPLETED  # Partial success
            final_result["partial"] = True
            final_result["failed_tasks"] = dag.failed_count
        else:
            workflow.status = WorkflowStatus.COMPLETED

        if synthesis_failed:
            final_result["synthesis_failed"] = True
        workflow.completed_at = datetime.now(UTC)
        workflow.result = final_result
        workflow.dag_snapshot = dag.to_dict()
        await self.session.flush()

        await self._publish_progress(workflow, dag, "workflow_completed")

        logger.info(
            "workflow_completed",
            workflow_id=str(workflow.workflow_id),
            completed=dag.completed_count,
            failed=dag.failed_count,
            total=len(dag.nodes),
            budget_used=float(workflow.budget_used),
        )

        # Cleanup tool workspace
        import shutil

        settings = get_settings()
        workspace = os.path.join(settings.tool_workspace_base, str(workflow.workflow_id))
        shutil.rmtree(workspace, ignore_errors=True)

        return final_result

    async def _policy_evaluate_nodes(
        self,
        *,
        workflow: Workflow,
        active_nodes: list[DAGNode],
        node_contexts: dict[str, tuple[Task, Agent, dict]],
    ) -> list[DAGNode]:
        """Run the policy registry for each node, return those that need HITL.

        Side effects:
        - Nodes whose aggregated decision is ``DENY`` are marked
          :class:`TaskStatus.SKIPPED`, removed from ``node_contexts``,
          and their reason is copied onto ``task.error_message``.
        - Nodes whose decision is ``ESCALATE_HUMAN`` are returned so
          the caller can feed them into the approval gate.
        - ``ALLOW`` and ``ABSTAIN`` are no-ops: the scheduler proceeds.
        """
        from app.safety.audit import record_decision
        from app.safety.context import PolicyContext
        from app.safety.decision import DecisionAction
        from app.safety.registry import PolicyRegistry
        from app.safety.registry import evaluate as policy_evaluate
        from app.services.identity_service import get_instance_key_store

        # Fast path: nothing registered → skip the overhead entirely.
        if not PolicyRegistry.all():
            return []

        # Grab the instance keypair once — each non-abstain decision
        # gets persisted as a signed audit record. If signing is
        # unavailable, evaluation still runs; we just lose that record.
        try:
            audit_keypair = get_instance_key_store().instance_keypair()
        except Exception as exc:
            logger.warning("policy_audit_keypair_unavailable", error=str(exc))
            audit_keypair = None

        escalated: list[DAGNode] = []
        for node in active_nodes:
            task_rec, agent, _ = node_contexts[node.node_id]
            ctx = PolicyContext(
                agent_did=None,
                agent_trust_score=float(agent.trust_score) if agent is not None else None,
                agent_capabilities=tuple(agent.capabilities or ()),
                agent_id=agent.agent_id if agent is not None else None,
                action=f"execute_{node.capability}",
                action_category=node.capability,
                tool_name=None,
                workflow_id=workflow.workflow_id,
                task_id=task_rec.task_id,
                session=self.session,
                redis=self.redis,
                extra={"description": node.description},
            )
            try:
                decision = await policy_evaluate(ctx)
            except Exception as exc:
                logger.warning(
                    "policy_evaluation_failed",
                    workflow_id=str(workflow.workflow_id),
                    node_id=node.node_id,
                    error=str(exc),
                )
                continue

            if audit_keypair is not None:
                await record_decision(
                    session=self.session,
                    keypair=audit_keypair,
                    decision=decision,
                    ctx=ctx,
                )

            if decision.action is DecisionAction.DENY:
                logger.info(
                    "policy_denied_task",
                    workflow_id=str(workflow.workflow_id),
                    node_id=node.node_id,
                    rule_id=decision.rule_id,
                    reason=decision.reason,
                )
                task_rec.status = TaskStatus.SKIPPED
                task_rec.error_message = f"Policy denied ({decision.rule_id}): {decision.reason}"
                await self.session.flush()
                node_contexts.pop(node.node_id, None)
            elif decision.action is DecisionAction.ESCALATE_HUMAN:
                logger.info(
                    "policy_escalated_task",
                    workflow_id=str(workflow.workflow_id),
                    node_id=node.node_id,
                    rule_id=decision.rule_id,
                    reason=decision.reason,
                )
                escalated.append(node)

        return escalated

    async def _reflect_on_task(
        self,
        *,
        workflow: Workflow,
        dag: DAG,
        node: DAGNode,
        task_rec: Task,
        response_content: str,
    ) -> dict[str, Any] | None:
        """Four-branch adaptive-intelligence dispatch after a task completes.

        Runs the reflection engine and, based on the returned ``action``,
        may:
            - do nothing (``accept``)
            - delegate to :class:`SelfCorrectionEngine` for a retry decision
              and reset the DAG node so the scheduler re-executes it
            - delegate to :class:`DynamicReplanner` to inject an alternative
              node (``re_plan`` / ``add_verifier``)
            - persist a workflow checkpoint and let the outer loop surface
              the failure (``abort``)

        Returns a dict with ``continue_loop=True`` when the caller should
        ``continue`` its outer loop (i.e. the task was reset for retry);
        ``None`` otherwise.
        """
        from app.intelligence.dynamic_replanner import DynamicReplanner
        from app.intelligence.reflection import ReflectionEngine
        from app.intelligence.self_correction import SelfCorrectionEngine
        from app.orchestration.checkpoint import CheckpointStore, WorkflowCheckpoint
        from app.services.identity_service import get_instance_key_store

        # Feed the engine our Redis client so repeated reflections on
        # the same (goal, output) pair short-circuit the LLM — important
        # for retries where the task output hasn't actually changed yet.
        reflection_engine = ReflectionEngine(redis=self.redis)
        task_outputs = [{"capability": node.capability, "content": response_content[:2000]}]
        reflection = await reflection_engine.reflect(
            goal_description=workflow.prompt,
            completed_outputs=task_outputs,
        )

        if not isinstance(task_rec.output, dict):
            task_rec.output = {}
        task_rec.output["reflection"] = {
            "action": reflection.action,
            "confidence": reflection.confidence,
            "issues": reflection.issues[:3] if reflection.issues else [],
            "suggestions": reflection.suggestions[:3] if reflection.suggestions else [],
        }
        from sqlalchemy.orm.attributes import flag_modified as _flag_modified

        _flag_modified(task_rec, "output")

        # ---------- accept: nothing to do
        if reflection.action == "accept":
            await self.session.flush()
            return None

        # ---------- retry_task: delegate to SelfCorrectionEngine
        if reflection.action == "retry_task":
            retry_count = int(task_rec.retry_count or 0)
            corrector = SelfCorrectionEngine()
            decision = corrector.evaluate(
                task_output=response_content,
                reflection_action=reflection.action,
                reflection_confidence=reflection.confidence,
                reflection_issues=list(reflection.issues or []),
                reflection_suggestions=list(reflection.suggestions or []),
                retry_count=retry_count,
            )
            if decision.should_retry:
                task_rec.retry_count = retry_count + 1
                task_rec.status = TaskStatus.PENDING
                task_rec.output["retry_feedback"] = decision.improved_prompt or ""
                # Fix D companion — Day 22: clear the review flag so if the
                # new attempt also produces low confidence the review gate
                # can fire exactly once more.
                task_rec.output.pop("review_requested", None)
                _flag_modified(task_rec, "output")
                dag.mark_pending(node.node_id)
                logger.info(
                    "task_retry_triggered",
                    node_id=node.node_id,
                    confidence=reflection.confidence,
                    retry=task_rec.retry_count,
                    reason=decision.reason,
                )
                await self.session.flush()
                return {"continue_loop": True, "action": "retry_task"}
            logger.info(
                "task_retry_declined",
                node_id=node.node_id,
                reason=decision.reason,
                retry_count=retry_count,
            )
            await self.session.flush()
            return None

        # ---------- re_plan / add_verifier: inject an alternative node
        if reflection.action in ("re_plan", "add_verifier"):
            context = "; ".join((reflection.issues or [])[:3]) or reflection.action
            # Cast add_verifier to re_plan for the replanner which only
            # reacts to the re_plan string. The intent is the same:
            # inject an alternative task node.
            new_nodes = DynamicReplanner.replan(
                dag,
                failed_node_id=node.node_id,
                reflection_action="re_plan",
                context=context,
            )
            if new_nodes:
                task_rec.output["replan"] = {
                    "added_node_ids": [n.node_id for n in new_nodes],
                    "action": reflection.action,
                    "context": context,
                }
                _flag_modified(task_rec, "output")
                logger.info(
                    "task_replan_added_alternative",
                    original=node.node_id,
                    added=[n.node_id for n in new_nodes],
                    action=reflection.action,
                )
            else:
                logger.info(
                    "task_replan_skipped",
                    node_id=node.node_id,
                    action=reflection.action,
                )
            await self.session.flush()
            return None

        # ---------- abort: save a checkpoint so the workflow can be resumed post-mortem
        if reflection.action == "abort":
            checkpoint_hash: str | None = None
            try:
                keypair = get_instance_key_store().instance_keypair()
                store = CheckpointStore(session=self.session, keypair=keypair)
                checkpoint = WorkflowCheckpoint.from_dag(
                    workflow_id=workflow.workflow_id,
                    dag=dag,
                    reason=f"reflection_abort: {', '.join((reflection.issues or [])[:2])}",
                    current_task_id=node.node_id,
                    variables={},
                    reflection={
                        "action": reflection.action,
                        "confidence": reflection.confidence,
                        "issues": list(reflection.issues or [])[:5],
                        "suggestions": list(reflection.suggestions or [])[:5],
                    },
                )
                saved = await store.save(checkpoint)
                checkpoint_hash = saved.content_hash
                task_rec.output["abort_checkpoint"] = {
                    "hash": saved.content_hash,
                    "reason": saved.reason,
                }
                _flag_modified(task_rec, "output")
                logger.warning(
                    "task_reflection_abort",
                    workflow_id=str(workflow.workflow_id),
                    node_id=node.node_id,
                    checkpoint_hash=(saved.content_hash or "")[:12],
                )
            except Exception as exc:
                logger.warning(
                    "task_reflection_abort_checkpoint_failed",
                    node_id=node.node_id,
                    error=str(exc),
                )

            # Transition the workflow itself to a terminal FAILED state so
            # /status polling doesn't loop forever on "pending". Previously
            # the abort only flagged the task; the workflow sat in RUNNING
            # indefinitely and any stock agent polling /status would give up
            # by timeout with no diagnosis. Surfaced by OpenClaw harness s8.
            issues_summary = "; ".join((reflection.issues or [])[:3]) or "safety check failed"
            suggestions_summary = "; ".join((reflection.suggestions or [])[:2])
            workflow.status = WorkflowStatus.FAILED
            workflow.completed_at = datetime.now(UTC)
            workflow.result = {
                "status": "aborted_by_safety_reflection",
                "aborted_at_task": node.node_id,
                "aborted_at_capability": node.capability,
                "issues": issues_summary,
                "suggestions": suggestions_summary,
                "checkpoint_hash": checkpoint_hash,
                "next_steps": (
                    "The safety reflection engine refused to execute this workflow. "
                    "If you believe the block is a false positive, rephrase the request "
                    "with explicit authorization context or submit it via the HITL "
                    "approval flow using the checkpoint_hash above."
                ),
            }
            _flag_modified(workflow, "result")
            await self._publish_progress(
                workflow,
                dag,
                "workflow_aborted_by_safety",
                node=node,
                extra={"checkpoint_hash": checkpoint_hash, "issues": issues_summary},
            )
            await self.session.flush()
            return None

        # Unknown action — log and continue without action.
        logger.info(
            "task_reflection_unknown_action",
            node_id=node.node_id,
            action=reflection.action,
        )
        await self.session.flush()
        return None

    async def _handle_failure(
        self,
        workflow: Workflow,
        dag: DAG,
        node: DAGNode,
        agents: dict[str, Agent],
        error: Exception,
    ) -> None:
        """Handle a failed node: mark failed and skip all dependents."""
        logger.warning(
            "task_failed",
            node_id=node.node_id,
            capability=node.capability,
            error=str(error),
            error_type=type(error).__name__,
        )

        skipped = dag.mark_failed(node.node_id, error=str(error))
        if skipped:
            logger.info("tasks_skipped_dependency", node_id=node.node_id, skipped=skipped)

        await self._publish_progress(workflow, dag, "task_failed", node=node)

    async def _run_tool_loop(
        self,
        *,
        node: DAGNode,
        agent: Agent,
        workflow: Workflow,
        user_prompt: str,
        system_prompt: str,
        model: str,
        max_tok: int,
    ) -> LLMResponse:
        """Run Claude with tools for up to ``tool_max_iterations`` rounds.

        Fix A — Day 22: extracted from ``execute_workflow`` so the
        tool-loop-exhaustion path is unit-testable. When the loop hits
        max iterations with Claude still requesting tools, we force ONE
        final ``tools=None`` call so the model must synthesise a text
        answer from the tool results it already has. If that also
        produces empty content we return a response with ``content=""``
        and the caller marks the node FAILED rather than COMPLETED-with-
        junk.
        """
        from app.tools.base import ToolContext
        from app.tools.executor import ToolExecutor
        from app.tools.registry import get_tool_registry

        registry = get_tool_registry()
        # Ask the registry for the agent's toolbelt: prefer `agent.supported_tools`
        # (authoritative allowlist maintained by the operator) with capability-based
        # unlock as fallback. Pre-2026-04-17 we only used capabilities here, which
        # caused every code_execute request to return fabricated output because no
        # seeded agent had the "code-execution" capability. See
        # ToolRegistry.get_tools_for_agent docstring.
        available_tools = registry.get_tools_for_agent(agent)
        tool_schemas = [t.to_schema() for t in available_tools] if available_tools else None

        settings = get_settings()
        max_iterations = settings.tool_max_iterations
        all_tool_calls: list[dict] = []
        current_prompt = user_prompt
        total_cost = Decimal("0")
        tool_results_text: list[str] = []
        resp: LLMResponse | None = None

        for iteration in range(max_iterations):
            resp = await self.model_router.generate(
                prompt=current_prompt,
                system=system_prompt,
                model=model,
                max_tokens=max_tok,
                temperature=0.4,
                tools=tool_schemas,
            )
            total_cost += resp.cost

            if not resp.tool_calls:
                resp.cost = total_cost
                if all_tool_calls:
                    resp.metadata["tool_calls"] = all_tool_calls
                    resp.metadata["tool_iterations"] = iteration + 1
                await self._store_in_cache(node, user_prompt, resp)
                return resp

            tool_executor = ToolExecutor(session=self.session, redis=self.redis)
            tool_context_obj = ToolContext(
                workflow_id=workflow.workflow_id,
                agent_id=agent.agent_id,
                workspace_path=f"{settings.tool_workspace_base}/{workflow.workflow_id}",
            )
            tool_results_text = []
            for call in resp.tool_calls:
                result = await tool_executor.execute_tool(call.name, call.arguments, tool_context_obj)
                all_tool_calls.append(
                    {
                        "tool": call.name,
                        "arguments": call.arguments,
                        "result": result.content[:500],
                        "success": result.success,
                    }
                )
                tool_results_text.append(f"## Tool Result: {call.name}\nSuccess: {result.success}\n{result.content}\n")
                total_cost += Decimal(str(result.cost_credits))

            current_prompt = (
                f"{user_prompt}\n\n"
                f"## Tool Execution Results (iteration {iteration + 1})\n\n"
                + "\n".join(tool_results_text)
                + "\n\nContinue your analysis using these tool results. "
                "If you need more information, call another tool. "
                "Otherwise, provide your final response."
            )

        # Fix A — Day 22: exhaustion fallback. Force a tools=None summary
        # call so Claude can't keep requesting tools.
        summary_prompt = (
            f"{user_prompt}\n\n"
            f"## Tool Execution Results (all {max_iterations} iterations)\n\n"
            + "\n".join(tool_results_text)
            + "\n\nYou have reached the tool-use budget. Do not request any more tools. "
            "Synthesise your final response from the tool results above. "
            "If the information is insufficient, say so clearly and stop."
        )
        try:
            summary = await self.model_router.generate(
                prompt=summary_prompt,
                system=system_prompt,
                model=model,
                max_tokens=max_tok,
                temperature=0.4,
                tools=None,
            )
            total_cost += summary.cost
            summary.cost = total_cost
            summary.metadata["tool_calls"] = all_tool_calls
            summary.metadata["tool_iterations"] = max_iterations
            summary.metadata["max_iterations_reached"] = True
            summary.metadata["summary_forced"] = True
            if summary.content.strip():
                await self._store_in_cache(node, user_prompt, summary)
                return summary
            resp = summary
        except Exception as e:
            logger.warning("tool_loop_summary_call_failed", error=str(e), node_id=node.node_id)

        # Empty summary → return content="" so caller marks node FAILED.
        assert resp is not None, "tool loop must have produced at least one response"
        resp.cost = total_cost
        resp.metadata["tool_calls"] = all_tool_calls
        resp.metadata["tool_iterations"] = max_iterations
        resp.metadata["max_iterations_reached"] = True
        resp.metadata["summary_forced"] = True
        resp.metadata["summary_empty"] = True
        resp.content = ""
        return resp

    async def _upsert_task_row(
        self,
        *,
        node: DAGNode,
        workflow_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        step_number: int,
        status: TaskStatus,
        error_message: str | None,
    ) -> Task:
        """Re-use the Task row for a node if it already exists; else create.

        The DAG node tracks its task_id after the first creation
        (``node.task_id``). On retry branches — ``dag.mark_pending()``
        resets the node but keeps the same ``node.task_id`` — we want to
        update the existing row rather than stamp a new one. Without
        this, the outer scheduler loop can stamp 40+ rows for a 5-node
        DAG when the tool-loop fails to converge.

        Idempotent: calling twice with identical inputs is a no-op
        beyond setting the requested ``status`` + ``error_message``.
        """
        existing: Task | None = None
        if node.task_id is not None:
            existing = await self.session.get(Task, node.task_id)

        if existing is not None:
            existing.status = status
            existing.error_message = error_message
            if agent_id is not None:
                existing.assigned_agent_id = agent_id
            await self.session.flush()
            return existing

        task = Task(
            workflow_id=workflow_id,
            assigned_agent_id=agent_id,
            step_number=step_number,
            capability=node.capability,
            description=node.description,
            status=status,
            error_message=error_message,
        )
        self.session.add(task)
        await self.session.flush()
        await self.session.refresh(task)
        node.task_id = task.task_id
        return task

    async def _build_context(self, workflow: Workflow, node: DAGNode, dag: DAG) -> str:
        """Build context from shared memory for an agent task."""
        from app.schemas.memory import MemoryQueryRequest

        # Query memory for relevant context
        results = await self.memory_service.query_memory(
            MemoryQueryRequest(
                query=f"{node.capability}: {node.description}",
                workflow_id=workflow.workflow_id,
                min_similarity=0.3,
                top_k=10,
            )
        )

        if not results:
            return "No prior context available. This is the first task in the workflow."

        context_parts = []
        for r in results:
            context_parts.append(f"[{r.memory.memory_type} | similarity={r.similarity}]\n{r.memory.content}")

        return "\n\n---\n\n".join(context_parts)

    @staticmethod
    def _build_agent_prompt(node: DAGNode, context: str) -> str:
        """Build the user prompt for an agent, including task description and context."""
        return (
            f"## Task\n{node.description}\n\n"
            f"## Context from Prior Agents\n{context}\n\n"
            f"## Instructions\n"
            f"Complete this task thoroughly. Build on the context provided by prior agents. "
            f"Be specific, actionable, and thorough in your analysis."
        )

    @staticmethod
    def _estimate_node_cost(agent: Agent) -> float:
        """Estimate task cost in credits for budget reservation."""
        avg_cost_usd = (agent.cost_profile or {}).get("avg_cost_per_call", 0.01)
        return tokens_to_credits(Decimal(str(avg_cost_usd))) * 1.5  # 50% buffer

    async def _publish_progress(
        self,
        workflow: Workflow,
        dag: DAG,
        event_type: str,
        node: DAGNode | None = None,
        agent: Agent | None = None,
        extra: dict | None = None,
    ) -> None:
        """Publish real-time workflow progress event via Redis pub/sub."""
        data = {
            "workflow_id": str(workflow.workflow_id),
            "event": event_type,
            "progress_percent": dag.progress_percent,
            "completed": dag.completed_count,
            "failed": dag.failed_count,
            "total": len(dag.nodes),
        }
        if node:
            data["node_id"] = node.node_id
            data["capability"] = node.capability
            data["status"] = node.status.value
        if agent:
            data["agent_name"] = agent.name
            data["agent_id"] = str(agent.agent_id)
        if extra:
            data.update(extra)

        await EventBus.publish_dict(
            ["workflow", str(workflow.workflow_id), "progress"],
            f"workflow.{event_type}",
            data,
        )

    # ── Strategy 3: Semantic Result Cache ──

    async def _check_semantic_cache(self, node: DAGNode, prompt: str) -> LLMResponse | None:
        """Check if a semantically similar task has been completed recently.

        Uses pgvector to find completed task outputs with similar input prompts.
        Returns a synthetic LLMResponse if a high-similarity match is found.
        """
        try:
            from sqlalchemy import text

            from app.embeddings.service import get_embedding_service

            embedding_svc = get_embedding_service()
            if not embedding_svc or not embedding_svc.is_loaded:
                return None

            # Generate embedding for the current prompt
            prompt_embedding = await embedding_svc.embed(prompt[:500])

            # Format as pgvector-compatible string: [0.1,0.2,...]
            embedding_str = f"[{','.join(str(x) for x in prompt_embedding)}]"

            # Query memory records for similar completed task outputs
            # Use CAST() instead of :: to avoid SQLAlchemy bind-param collision
            result = await self.session.execute(
                text("""
                    SELECT content,
                           1 - (embedding <=> CAST(:embedding AS vector)) as similarity
                    FROM memory_records
                    WHERE memory_type = 'result'
                      AND confidence >= 0.8
                      AND created_at > NOW() - INTERVAL '24 hours'
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT 1
                """),
                {"embedding": embedding_str},
            )
            row = result.fetchone()

            if row and row.similarity >= 0.88:
                logger.info(
                    "semantic_cache_hit",
                    capability=node.capability,
                    similarity=round(row.similarity, 3),
                )
                return LLMResponse(
                    content=row.content,
                    model="cache",
                    input_tokens=0,
                    output_tokens=0,
                    cost=Decimal("0"),
                    latency_ms=0.5,
                    metadata={"stop_reason": "cache_hit", "provider": "cache"},
                )
            return None
        except Exception as e:
            # Cache miss on error — fall through to LLM call
            logger.debug("semantic_cache_error", error=str(e))
            return None

    async def _store_in_cache(self, node: DAGNode, prompt: str, response: LLMResponse) -> None:
        """Store a task result in the semantic cache for future reuse.

        Writes to memory_records with the prompt embedding so future similar
        queries can hit the cache via _check_semantic_cache().
        """
        try:
            if not response.content or len(response.content) < 50:
                return  # Don't cache trivial responses

            from app.embeddings.service import get_embedding_service

            embedding_svc = get_embedding_service()
            if not embedding_svc or not embedding_svc.is_loaded:
                return

            # Generate embedding of the prompt (not the response) for lookup matching
            prompt_embedding = await embedding_svc.embed(prompt[:500])

            from app.models.memory import MemoryRecord
            from app.models.memory import MemoryType as MemType

            cache_record = MemoryRecord(
                workflow_id=None,  # Cache entries are cross-workflow
                source_agent_id=None,
                memory_type=MemType.RESULT,
                content=response.content[:5000],  # Cap stored content
                embedding=prompt_embedding,
                confidence=0.85,
                metadata_={
                    "capability": node.capability,
                    "model": response.model,
                    "cached": True,
                },
            )
            self.session.add(cache_record)
            await self.session.flush()
            logger.debug(
                "semantic_cache_stored",
                capability=node.capability,
                content_length=len(response.content),
            )
        except Exception as e:
            logger.debug("semantic_cache_store_error", error=str(e))
