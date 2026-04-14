"""MassClaw MCP Server — exposes MassClaw capabilities as MCP tools."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions (static metadata only — no DB access at import time)
# ---------------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "submit_workflow",
        "description": (
            "Submit a new workflow to MassClaw for multi-agent execution. Returns the workflow ID and initial status."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The task or goal for the workflow (min 10 chars).",
                    "minLength": 10,
                },
                "user_id": {
                    "type": "string",
                    "description": "Identifier for the requesting user.",
                    "default": "mcp-user",
                },
                "domain": {
                    "type": "string",
                    "description": "Domain hint (e.g. 'research', 'code'). Auto-detected if omitted.",
                },
                "budget_limit": {
                    "type": "number",
                    "description": "Maximum budget in MassClaw credits (must be > 0).",
                    "exclusiveMinimum": 0,
                },
                "priority": {
                    "type": "integer",
                    "description": "Workflow priority 1–10 (1 = highest).",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
            },
            "required": ["prompt", "budget_limit"],
        },
    },
    {
        "name": "get_workflow_status",
        "description": ("Get the current status and progress of a running or completed workflow."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "UUID of the workflow to query.",
                },
            },
            "required": ["workflow_id"],
        },
    },
    {
        "name": "get_workflow_result",
        "description": ("Retrieve the final synthesized result of a completed workflow."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "UUID of the completed workflow.",
                },
            },
            "required": ["workflow_id"],
        },
    },
    {
        "name": "search_agents",
        "description": ("Search the MassClaw agent registry for agents that match given capabilities."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of required capabilities.",
                    "minItems": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of agents to return.",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                },
            },
            "required": ["capabilities"],
        },
    },
    {
        "name": "query_memory",
        "description": ("Semantic search over the MassClaw memory store using natural-language queries."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query.",
                    "minLength": 1,
                },
                "workflow_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Scope search to a specific workflow (optional).",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return.",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 10,
                },
                "min_similarity": {
                    "type": "number",
                    "description": "Minimum cosine similarity threshold [0, 1].",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.5,
                },
            },
            "required": ["query"],
        },
    },
]


class MassClawMCPServer:
    """MCP server that exposes MassClaw services as callable tools.

    Tools are dispatched via :meth:`handle_tool_call`.  Each handler obtains
    a fresh DB session and Redis client so the server can be used from any
    async context (workers, API routes, etc.).
    """

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {t["name"]: t for t in _TOOLS}

    # ------------------------------------------------------------------
    # Tool listing
    # ------------------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        """Return the list of tool definitions for MCP protocol negotiation."""
        return list(self._tools.values())

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> Any:
        """Dispatch *name* to the appropriate handler with *arguments*.

        Raises ValueError for unknown tool names.
        """
        handler = {
            "submit_workflow": self._submit_workflow,
            "get_workflow_status": self._get_workflow_status,
            "get_workflow_result": self._get_workflow_result,
            "search_agents": self._search_agents,
            "query_memory": self._query_memory,
        }.get(name)

        if handler is None:
            raise ValueError(f"Unknown MCP tool: '{name}'")

        logger.info("mcp_server_tool_call", tool=name)
        return await handler(arguments)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _submit_workflow(self, args: dict[str, Any]) -> dict[str, Any]:
        from app.core.database import db_session_context
        from app.core.redis import get_redis_manager
        from app.schemas.workflow import WorkflowCreate
        from app.services.workflow_service import WorkflowService

        data = WorkflowCreate(
            prompt=args["prompt"],
            user_id=args.get("user_id", "mcp-user"),
            domain=args.get("domain"),
            budget_limit=float(args["budget_limit"]),
            priority=int(args.get("priority", 5)),
        )
        redis = get_redis_manager().get_cache_client()
        async with db_session_context() as session:
            svc = WorkflowService(session, redis)
            workflow = await svc.create_and_execute(data)
            return {
                "workflow_id": str(workflow.workflow_id),
                "status": workflow.status.value if hasattr(workflow.status, "value") else str(workflow.status),
            }

    async def _get_workflow_status(self, args: dict[str, Any]) -> dict[str, Any]:
        from app.core.database import db_session_context
        from app.core.redis import get_redis_manager
        from app.services.workflow_service import WorkflowService

        try:
            workflow_id = uuid.UUID(args["workflow_id"])
        except (ValueError, KeyError) as e:
            return {"error": f"Invalid workflow_id: {e}"}
        redis = get_redis_manager().get_cache_client()
        async with db_session_context() as session:
            svc = WorkflowService(session, redis)
            status_resp = await svc.get_workflow_status(workflow_id)
            return status_resp.model_dump(mode="json")

    async def _get_workflow_result(self, args: dict[str, Any]) -> dict[str, Any]:
        from app.core.database import db_session_context
        from app.core.redis import get_redis_manager
        from app.services.workflow_service import WorkflowService

        try:
            workflow_id = uuid.UUID(args["workflow_id"])
        except (ValueError, KeyError) as e:
            return {"error": f"Invalid workflow_id: {e}"}
        redis = get_redis_manager().get_cache_client()
        async with db_session_context() as session:
            svc = WorkflowService(session, redis)
            workflow = await svc.get_workflow(workflow_id)
            return {
                "workflow_id": str(workflow.workflow_id),
                "result": workflow.result,
                "status": workflow.status.value if hasattr(workflow.status, "value") else str(workflow.status),
            }

    async def _search_agents(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        from app.core.database import db_session_context
        from app.core.redis import get_redis_manager
        from app.schemas.agent import AgentSearchParams
        from app.services.agent_service import AgentService

        capabilities: list[str] = args["capabilities"]
        limit: int = int(args.get("limit", 20))
        redis = get_redis_manager().get_cache_client()
        async with db_session_context() as session:
            svc = AgentService(session, redis)
            params = AgentSearchParams(capabilities=capabilities)
            agents = await svc.search_agents(params, limit=limit)
            return [
                {
                    "agent_id": str(a.agent_id),
                    "name": a.name,
                    "capabilities": a.capabilities,
                    "trust_score": float(a.trust_score),
                    "endpoint": a.endpoint,
                }
                for a in agents
            ]

    async def _query_memory(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        from app.core.database import db_session_context
        from app.core.redis import get_redis_manager
        from app.schemas.memory import MemoryQueryRequest
        from app.services.memory_service import MemoryService

        query_req = MemoryQueryRequest(
            query=args["query"],
            workflow_id=uuid.UUID(args["workflow_id"]) if args.get("workflow_id") else None,
            top_k=int(args.get("top_k", 10)),
            min_similarity=float(args.get("min_similarity", 0.5)),
        )
        redis = get_redis_manager().get_cache_client()
        async with db_session_context() as session:
            svc = MemoryService(session, redis)
            results = await svc.query_memory(query_req)
            return [
                {
                    "memory_id": str(r.memory.memory_id),
                    "content": r.memory.content,
                    "similarity": r.similarity,
                    "relevance_score": r.relevance_score,
                    "memory_type": r.memory.memory_type.value
                    if hasattr(r.memory.memory_type, "value")
                    else str(r.memory.memory_type),
                }
                for r in results
            ]
