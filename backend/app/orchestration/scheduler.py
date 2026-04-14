from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal

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

                # Pre-execution budget check — fail fast before wasting LLM calls
                estimated_cost = self._estimate_node_cost(agent)
                try:
                    reservation = await self.wallet_service.reserve_budget(
                        workflow_id=workflow.workflow_id,
                        agent_id=agent.agent_id,
                        estimated_cost=estimated_cost,
                        reason=f"Task {node.node_id}: {node.capability}",
                    )
                    reservations[node.node_id] = reservation
                except BudgetExhaustedError:
                    dag.mark_failed(
                        node.node_id,
                        error=f"Budget insufficient for {node.capability}: need ~{estimated_cost:.1f} cr",
                    )
                    # Create a task record so the UI shows it as failed
                    failed_task = Task(
                        workflow_id=workflow.workflow_id,
                        assigned_agent_id=agent.agent_id,
                        step_number=int("".join(c for c in node.node_id if c.isdigit()) or "0"),
                        capability=node.capability,
                        description=node.description,
                        status=TaskStatus.FAILED,
                        error_message=f"Budget insufficient: need ~{estimated_cost:.1f} cr",
                    )
                    self.session.add(failed_task)
                    await self.session.flush()
                    logger.warning(
                        "task_budget_insufficient",
                        node_id=node.node_id,
                        capability=node.capability,
                        estimated_cost=estimated_cost,
                    )
                    continue

                dag.mark_running(node.node_id)

                task = Task(
                    workflow_id=workflow.workflow_id,
                    assigned_agent_id=agent.agent_id,
                    step_number=int("".join(c for c in node.node_id if c.isdigit()) or "0"),
                    capability=node.capability,
                    description=node.description,
                    status=TaskStatus.RUNNING,
                )
                self.session.add(task)
                await self.session.flush()
                await self.session.refresh(task)
                node.task_id = task.task_id

                # Build context from shared memory (DB read)
                context = await self._build_context(workflow, node, dag)
                node_contexts[node.node_id] = (task, agent, context)

                await self._publish_progress(workflow, dag, "task_started", node=node, agent=agent)

            # Phase 1.5: Approval gate for high-risk capabilities
            APPROVAL_REQUIRED_CAPABILITIES = {"risk-assessment", "compliance-check", "safety-analysis"}
            active_nodes = [n for n in ready_nodes if n.node_id in node_contexts]
            nodes_needing_approval = [n for n in active_nodes if n.capability in APPROVAL_REQUIRED_CAPABILITIES]

            if nodes_needing_approval:
                from app.core.redis import get_redis_manager
                from app.safety.approval import ApprovalManager

                approval_mgr = ApprovalManager(get_redis_manager().get_cache_client())

                for node in nodes_needing_approval:
                    task_rec = node_contexts[node.node_id][0]
                    agent = node_contexts[node.node_id][1]
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
                    )

                    # Non-blocking: check if auto-approved by policy, otherwise mark as awaiting
                    try:
                        decision = await approval_mgr.check_status(request.request_id)
                    except ValueError:
                        decision = None

                    if decision and decision.status == "pending":
                        task_rec.status = TaskStatus.AWAITING_APPROVAL
                        task_rec.error_message = f"Awaiting human approval (request: {request.request_id})"
                        await self.session.flush()

                        workflow.status = WorkflowStatus.PAUSED
                        await self.session.flush()

                        await self._publish_progress(
                            workflow,
                            dag,
                            "approval_required",
                            node=node,
                            extra={
                                "approval_id": request.request_id,
                                "capability": node.capability,
                            },
                        )

                        # Wait for decision (with timeout)
                        final = await approval_mgr.wait_for_decision(request.request_id, timeout=300)

                        workflow.status = WorkflowStatus.RUNNING
                        await self.session.flush()

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

                # Resolve tools for this agent's capabilities
                from app.tools.base import ToolContext
                from app.tools.executor import ToolExecutor
                from app.tools.registry import get_tool_registry

                registry = get_tool_registry()
                agent_caps = agent.capabilities if isinstance(agent.capabilities, list) else []
                available_tools = registry.get_tools_for_capabilities(agent_caps)
                tool_schemas = [t.to_schema() for t in available_tools] if available_tools else None

                settings = get_settings()
                max_iterations = settings.tool_max_iterations
                all_tool_calls: list[dict] = []
                current_prompt = user_prompt
                total_cost = Decimal("0")

                for iteration in range(max_iterations):
                    try:
                        resp = await self.model_router.generate(
                            prompt=current_prompt,
                            system=system_prompt,
                            model=model,
                            max_tokens=max_tok,
                            temperature=0.4,
                            tools=tool_schemas,
                        )
                        total_cost += resp.cost
                    except Exception as e:
                        return node, e

                    # No tool calls — final response
                    if not resp.tool_calls:
                        resp.cost = total_cost
                        if all_tool_calls:
                            resp.metadata["tool_calls"] = all_tool_calls
                            resp.metadata["tool_iterations"] = iteration + 1
                        await self._store_in_cache(node, user_prompt, resp)
                        return node, resp

                    # Execute tool calls
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
                        tool_results_text.append(
                            f"## Tool Result: {call.name}\nSuccess: {result.success}\n{result.content}\n"
                        )
                        total_cost += Decimal(str(result.cost_credits))

                    # Append tool results for next iteration
                    current_prompt = (
                        f"{user_prompt}\n\n"
                        f"## Tool Execution Results (iteration {iteration + 1})\n\n"
                        + "\n".join(tool_results_text)
                        + "\n\nContinue your analysis using these tool results. "
                        "If you need more information, call another tool. "
                        "Otherwise, provide your final response."
                    )

                # Max iterations reached — return last response
                resp.cost = total_cost
                resp.metadata["tool_calls"] = all_tool_calls
                resp.metadata["tool_iterations"] = max_iterations
                resp.metadata["max_iterations_reached"] = True
                await self._store_in_cache(node, user_prompt, resp)
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

                    # Post-execution review gate for low-confidence outputs
                    if task_rec.confidence is not None and task_rec.confidence < 0.4:
                        try:
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
                            logger.info(
                                "low_confidence_review_requested",
                                node_id=node.node_id,
                                confidence=task_rec.confidence,
                            )
                        except Exception as e:
                            logger.warning("review_request_failed", error=str(e))

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
