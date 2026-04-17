from __future__ import annotations

from app.core.logging import get_logger
from app.tools.base import ToolProvider

logger = get_logger(__name__)

_registry: ToolRegistry | None = None


class ToolRegistry:
    """Singleton registry for all available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolProvider] = {}
        self._load_built_in_tools()

    def _load_built_in_tools(self) -> None:
        from app.tools.api_caller import APICallerTool
        from app.tools.code_execution import CodeExecutionTool
        from app.tools.file_io import FileListTool, FileReadTool, FileWriteTool
        from app.tools.web_search import WebScrapeTool, WebSearchTool

        built_ins: list[ToolProvider] = [
            WebSearchTool(),
            WebScrapeTool(),
            CodeExecutionTool(),
            FileReadTool(),
            FileWriteTool(),
            FileListTool(),
            APICallerTool(),
        ]
        for tool in built_ins:
            self.register(tool)
        logger.info("tool_registry_initialized", count=len(self._tools))

    def register(self, tool: ToolProvider) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolProvider | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolProvider]:
        return list(self._tools.values())

    def get_tools_for_capabilities(self, capabilities: list[str]) -> list[ToolProvider]:
        """Return tools accessible by an agent with the given capabilities.

        Returns an empty list if *capabilities* is empty — agents with no
        capabilities get no tool access.
        """
        cap_set = set(capabilities)
        return [t for t in self._tools.values() if t.required_capabilities & cap_set]

    def get_tools_for_agent(self, agent) -> list[ToolProvider]:
        """Return the toolbelt the agent's LLM is allowed to see at runtime.

        Two sources of truth, in order of precedence:

        1. ``agent.supported_tools`` — an explicit allowlist (dict or list)
           maintained by the operator. When set, this is the authoritative
           toolbelt: only tools named in it (and truthy if dict-valued)
           are exposed. This is how an operator restricts a risk agent to
           read-only access without reshaping its capability list.
        2. Fallback to capability-based unlock via
           :meth:`get_tools_for_capabilities` using ``agent.capabilities``.
           Legacy path, kept because early seed data left ``supported_tools``
           empty and migrating every existing row is out of scope.

        This split resolves a long-standing mismatch: ``supported_tools`` was
        stored and surfaced through AgentFacts but the scheduler never read
        it — so "agent X has code_execute" would be true in AgentFacts and
        in the DB, but false at dispatch time. Surfaced by the OpenClaw
        harness s5 run.
        """
        supported = getattr(agent, "supported_tools", None)
        if supported:
            # Dict form: {"code_execute": True, ...}. Any truthy value counts.
            # List form: ["code_execute", ...]. Presence counts.
            if isinstance(supported, dict):
                allowed_names = {name for name, flag in supported.items() if flag}
            elif isinstance(supported, (list, tuple, set)):
                allowed_names = set(supported)
            else:
                allowed_names = set()
            if allowed_names:
                return [t for name, t in self._tools.items() if name in allowed_names]
        caps = getattr(agent, "capabilities", None) or []
        return self.get_tools_for_capabilities(list(caps))


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
