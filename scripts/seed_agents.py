"""Seed the agent registry with development agents."""

import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.database import db_session_context, init_db, dispose_db
from app.core.redis import init_redis, dispose_redis
from app.models.agent import Agent
from app.models.base import AgentStatus
from sqlalchemy import select


# Every seeded agent gets the full set of built-in tools exposed through its
# agentic loop. MassClaw's tool layer gates by `agent.supported_tools` — if a
# tool isn't in this dict the agent's LLM simply never sees it in the tool
# list, so the planner can route a task to an agent by capability only for
# that agent to report "code_execute not available" at runtime (exactly what
# the OpenClaw harness caught on s5). Giving every agent every tool mirrors
# production reality where the tool registry IS the agent's toolbelt, and the
# capability match decides which agent picks up each task.
_ALL_BUILTIN_TOOLS: dict = {
    "web_search": True,
    "web_scrape": True,
    "code_execute": True,
    "file_read": True,
    "file_write": True,
    "file_list": True,
    "api_call": True,
}


SEED_AGENTS = [
    {
        "name": "Intake Agent",
        "description": "Intake specialist. Parses user requests and extracts scope, constraints, objectives, stakeholders, and success criteria.",
        "capabilities": ["intake", "classify", "extract-requirements"],
        "endpoint": "internal://intake-agent",
        "cost_profile": {"avg_cost_per_call": 0.003, "model": "claude-sonnet"},
        "latency_profile": {"p50_ms": 800, "p95_ms": 2000, "p99_ms": 4000},
        "trust_score": 0.85,
        "safety_level": 2,
        "version": "1.2.0",
    },
    {
        "name": "Research Agent",
        "description": "Research specialist. Gathers background context, benchmarks, industry data, and relevant patterns for the specified domain.",
        "capabilities": ["research", "data-retrieval", "literature-review", "code-execution"],
        "endpoint": "internal://research-agent",
        "cost_profile": {"avg_cost_per_call": 0.008, "model": "claude-sonnet"},
        "latency_profile": {"p50_ms": 2000, "p95_ms": 5000, "p99_ms": 8000},
        "trust_score": 0.80,
        "safety_level": 2,
        "version": "1.1.0",
    },
    {
        "name": "Process Mapping Agent",
        "description": "Workflow analysis specialist. Identifies process flow patterns, bottlenecks, resource constraints, and operational inefficiencies.",
        "capabilities": ["process-analysis", "workflow-mapping", "bottleneck-detection"],
        "endpoint": "internal://process-mapping-agent",
        "cost_profile": {"avg_cost_per_call": 0.010, "model": "claude-sonnet"},
        "latency_profile": {"p50_ms": 3000, "p95_ms": 6000, "p99_ms": 10000},
        "trust_score": 0.75,
        "safety_level": 3,
        "version": "1.0.0",
    },
    {
        "name": "Risk Agent",
        "description": "Risk assessment specialist. Identifies regulatory risks, compliance gaps, safety concerns, and operational vulnerabilities.",
        "capabilities": ["risk-assessment", "compliance-check", "safety-analysis"],
        "endpoint": "internal://risk-agent",
        "cost_profile": {"avg_cost_per_call": 0.012, "model": "claude-opus"},
        "latency_profile": {"p50_ms": 4000, "p95_ms": 8000, "p99_ms": 12000},
        "trust_score": 0.90,
        "safety_level": 5,
        "version": "2.0.0",
    },
    {
        "name": "Optimization Agent",
        "description": "Resource allocation optimizer. Proposes scheduling improvements, capacity adjustments, and resource reallocation strategies.",
        "capabilities": ["optimization", "scheduling", "resource-allocation", "code-execution", "data-analytics"],
        "endpoint": "internal://optimization-agent",
        "cost_profile": {"avg_cost_per_call": 0.010, "model": "claude-sonnet"},
        "latency_profile": {"p50_ms": 3000, "p95_ms": 7000, "p99_ms": 10000},
        "trust_score": 0.78,
        "safety_level": 3,
        "version": "1.3.0",
    },
    {
        "name": "Cost Agent",
        "description": "Financial impact analyst. Estimates costs of proposed changes, projects ROI, and evaluates budget implications of operational improvements.",
        "capabilities": ["cost-analysis", "budget-estimation", "roi-projection", "code-execution", "data-analytics"],
        "endpoint": "internal://cost-agent",
        "cost_profile": {"avg_cost_per_call": 0.006, "model": "claude-sonnet"},
        "latency_profile": {"p50_ms": 1500, "p95_ms": 3000, "p99_ms": 5000},
        "trust_score": 0.82,
        "safety_level": 3,
        "version": "1.1.0",
    },
    {
        "name": "Brief Agent",
        "description": "Executive summary generator. Synthesizes findings from all prior agents into a coherent, actionable executive operations brief.",
        "capabilities": ["summarization", "report-generation", "executive-brief"],
        "endpoint": "internal://brief-agent",
        "cost_profile": {"avg_cost_per_call": 0.008, "model": "claude-sonnet"},
        "latency_profile": {"p50_ms": 2000, "p95_ms": 4000, "p99_ms": 6000},
        "trust_score": 0.88,
        "safety_level": 2,
        "version": "1.4.0",
    },
    {
        "name": "Verifier Agent",
        "description": "Quality assurance and consistency auditor. Cross-checks outputs from all agents for logical consistency, factual accuracy, and completeness.",
        "capabilities": ["verification", "quality-check", "consistency-audit"],
        "endpoint": "internal://verifier-agent",
        "cost_profile": {"avg_cost_per_call": 0.015, "model": "claude-opus"},
        "latency_profile": {"p50_ms": 5000, "p95_ms": 10000, "p99_ms": 15000},
        "trust_score": 0.92,
        "safety_level": 5,
        "version": "2.1.0",
    },
]


async def seed() -> None:
    init_db()
    await init_redis()

    async with db_session_context() as session:
        # Check if agents already exist
        result = await session.execute(select(Agent).limit(1))
        if result.scalar_one_or_none():
            print("Agents already exist, skipping seed.")
            return

        for agent_data in SEED_AGENTS:
            agent = Agent(
                name=agent_data["name"],
                description=agent_data["description"],
                capabilities=agent_data["capabilities"],
                endpoint=agent_data["endpoint"],
                cost_profile=agent_data["cost_profile"],
                latency_profile=agent_data["latency_profile"],
                trust_score=agent_data["trust_score"],
                safety_level=agent_data["safety_level"],
                version=agent_data["version"],
                status=AgentStatus.ACTIVE,
                supported_tools=_ALL_BUILTIN_TOOLS,
            )
            session.add(agent)
            print(f"  + {agent.name} (trust={agent.trust_score})")

    print(f"\nSeeded {len(SEED_AGENTS)} agents.")

    await dispose_redis()
    from app.core.database import dispose_db
    await dispose_db()


if __name__ == "__main__":
    asyncio.run(seed())
