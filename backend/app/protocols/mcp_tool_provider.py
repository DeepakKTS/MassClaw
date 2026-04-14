"""MCPToolProvider — wraps an MCP server tool as a MassClaw ToolProvider."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult

if TYPE_CHECKING:
    from app.protocols.mcp_client import MCPClient

logger = get_logger(__name__)


class MCPToolProvider(ToolProvider):
    """Adapts a single MCP tool so it can be used via the MassClaw ToolRegistry.

    Tool name format: ``mcp:{server_name}:{tool_name}``
    """

    # MCP tools manage their own sandboxing — run in-process from our side.
    execution_mode = ExecutionMode.IN_PROCESS

    # Available to agents with any capabilities.
    required_capabilities: set[str] = set()

    def __init__(
        self,
        mcp_client: MCPClient,
        tool_name: str,
        description: str = "",
        parameters_schema: dict[str, Any] | None = None,
        estimated_cost_credits: float = 0.0,
    ) -> None:
        self._mcp_client = mcp_client
        self._tool_name = tool_name

        server_name = mcp_client.config.name
        self.name: str = f"mcp:{server_name}:{tool_name}"
        self.description: str = description or f"MCP tool '{tool_name}' from server '{server_name}'"
        self.parameters_schema: dict[str, Any] = parameters_schema or {}
        self.estimated_cost_credits: float = estimated_cost_credits

    # ------------------------------------------------------------------
    # ToolProvider interface
    # ------------------------------------------------------------------

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        """Delegate execution to the MCP server tool.

        Parses the MCP CallToolResult into a MassClaw ToolResult.
        """
        logger.debug(
            "mcp_tool_execute",
            tool=self.name,
            workflow_id=str(context.workflow_id),
        )
        try:
            mcp_result = await self._mcp_client.call_tool(self._tool_name, arguments)

            # Determine success from the MCP result
            is_error: bool = getattr(mcp_result, "isError", False) or False

            # Collect text content from the result
            content_parts: list[str] = []
            for item in getattr(mcp_result, "content", []) or []:
                if hasattr(item, "text"):
                    content_parts.append(item.text)
                elif isinstance(item, str):
                    content_parts.append(item)
                else:
                    content_parts.append(str(item))

            content = "\n".join(content_parts) if content_parts else ""

            return ToolResult(
                content=content,
                success=not is_error,
                metadata={
                    "server": self._mcp_client.config.name,
                    "tool": self._tool_name,
                },
            )

        except Exception as exc:
            logger.warning(
                "mcp_tool_execute_error",
                tool=self.name,
                error=str(exc),
            )
            return ToolResult(
                content=str(exc),
                success=False,
                metadata={
                    "server": self._mcp_client.config.name,
                    "tool": self._tool_name,
                    "error": str(exc),
                },
            )
