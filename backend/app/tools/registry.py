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
        """Return tools accessible by an agent with the given capabilities."""
        cap_set = set(capabilities)
        return [t for t in self._tools.values() if t.required_capabilities & cap_set]


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
