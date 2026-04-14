from __future__ import annotations

from fastapi import APIRouter

api_router = APIRouter()

from app.api.agents import router as agents_router
from app.api.memory import router as memory_router
from app.api.tasks import router as tasks_router
from app.api.trust import router as trust_router
from app.api.wallet import router as wallet_router
from app.api.workflows import router as workflows_router

api_router.include_router(agents_router, prefix="/agents", tags=["Agents"])
api_router.include_router(memory_router, prefix="/memory", tags=["Memory"])
api_router.include_router(tasks_router, prefix="/tasks", tags=["Tasks"])
api_router.include_router(trust_router, prefix="/trust", tags=["Trust"])
api_router.include_router(wallet_router, prefix="/wallet", tags=["Wallet"])
api_router.include_router(workflows_router, prefix="/workflows", tags=["Workflows"])

from app.api.audit import router as audit_router
from app.api.policy import router as policy_router

api_router.include_router(audit_router, prefix="/audit", tags=["Audit"])
api_router.include_router(policy_router, prefix="/policy", tags=["Policy"])

from app.api.evolution import router as evolution_router

api_router.include_router(evolution_router, prefix="/evolution", tags=["Evolution"])

from app.api.projects import router as projects_router

api_router.include_router(projects_router, prefix="/projects", tags=["Projects"])


@api_router.get("/", tags=["System"])
async def api_root() -> dict[str, str]:
    return {"message": "MassClaw API v1", "docs": "/docs"}


@api_router.get("/capabilities", tags=["Discovery"])
async def discover_capabilities() -> dict:
    """Discover MassClaw platform capabilities.

    This is the primary discovery endpoint for external agents (OpenClaw, stock agents).
    Returns what MassClaw can do, how many agents are available, and which endpoints to use.
    """
    from app.core.database import db_session_context
    from sqlalchemy import select, func
    from app.models.agent import Agent
    from app.models.base import AgentStatus
    from app.config import get_settings

    settings = get_settings()

    async with db_session_context() as session:
        # Count active agents
        count_result = await session.execute(
            select(func.count(Agent.agent_id)).where(Agent.status == AgentStatus.ACTIVE)
        )
        agent_count = count_result.scalar() or 0

        # Get all unique capabilities
        agents_result = await session.execute(
            select(Agent.capabilities).where(Agent.status == AgentStatus.ACTIVE)
        )
        all_caps = set()
        all_domains = set()
        for row in agents_result:
            caps = row[0]
            if isinstance(caps, list):
                all_caps.update(caps)

        # Get domains from recent workflows
        from app.models.workflow import Workflow
        domains_result = await session.execute(
            select(Workflow.domain).where(Workflow.domain.isnot(None)).distinct().limit(20)
        )
        for row in domains_result:
            if row[0]:
                all_domains.add(row[0])

    return {
        "platform": settings.app_name,
        "version": settings.app_version,
        "total_agents": agent_count,
        "capabilities": sorted(all_caps),
        "domains": sorted(all_domains) if all_domains else ["general"],
        "endpoints": {
            "submit_task": "POST /api/v1/workflows/submit",
            "list_agents": "GET /api/v1/agents",
            "search_agents": "GET /api/v1/agents/search?capability=<name>",
            "create_workflow": "POST /api/v1/workflows",
            "workflow_status": "GET /api/v1/workflows/{id}/status",
            "workflow_result": "GET /api/v1/workflows/{id}/result",
            "query_memory": "POST /api/v1/memory/query",
            "write_memory": "POST /api/v1/memory/write",
            "trust_breakdown": "GET /api/v1/trust/{agent_id}",
            "audit_trail": "GET /api/v1/audit/search",
            "policy_evaluate": "POST /api/v1/policy/evaluate",
        },
        "features": [
            "multi-agent orchestration",
            "semantic shared memory (pgvector)",
            "trust-aware agent selection",
            "budget-controlled execution",
            "policy-based safety (3-layer)",
            "real-time progress streaming (SSE)",
            "audit trail for every decision",
            "agent evolution and ranking",
        ],
        "auth": {
            "mode": "optional (demo) / required (production)",
            "methods": ["Bearer JWT", "X-API-Key"],
        },
    }
