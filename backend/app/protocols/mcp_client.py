"""MCP client — connects to external MCP servers and exposes their tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server process."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


class MCPClient:
    """Client for a single MCP server.

    Lifecycle::

        client = MCPClient(config)
        await client.connect()
        tools = await client.list_tools()
        result = await client.call_tool("tool_name", {"arg": "value"})
        await client.disconnect()
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._session: Any | None = None  # mcp.ClientSession
        self._cm: Any | None = None  # async context manager stack
        self._connected: bool = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """True if the client has an active MCP session."""
        return self._connected

    @property
    def config(self) -> MCPServerConfig:
        return self._config

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Spawn the MCP server process and initialise the session."""
        if self._connected:
            return

        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=self._config.command,
            args=self._config.args,
            env=self._config.env or None,
        )

        self._cm = AsyncExitStack()
        try:
            read, write = await self._cm.enter_async_context(stdio_client(params))
            session = await self._cm.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._session = session
            self._connected = True
            logger.info("mcp_client_connected", server=self._config.name)
        except Exception as exc:
            await self._cm.aclose()
            self._cm = None
            logger.error(
                "mcp_client_connect_failed",
                server=self._config.name,
                error=str(exc),
            )
            raise

    async def disconnect(self) -> None:
        """Tear down the MCP session and server process."""
        if self._cm is not None:
            try:
                await self._cm.aclose()
                logger.info("mcp_client_disconnected", server=self._config.name)
            except Exception as exc:
                logger.warning(
                    "mcp_client_disconnect_error",
                    server=self._config.name,
                    error=str(exc),
                )
            finally:
                self._cm = None
                self._session = None
                self._connected = False

    # ------------------------------------------------------------------
    # Tool operations
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the list of tool schemas exposed by the MCP server.

        Each item is a dict with at minimum ``name``, ``description``, and
        ``inputSchema`` keys (mirroring MCP's Tool model).
        """
        self._require_connected()
        result = await self._session.list_tools()
        tools: list[dict[str, Any]] = []
        for tool in result.tools:
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": (tool.inputSchema.model_dump() if tool.inputSchema else {}),
                }
            )
        return tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Invoke an MCP tool and return the raw CallToolResult."""
        self._require_connected()
        result = await self._session.call_tool(name, arguments or {})
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected or self._session is None:
            raise RuntimeError(f"MCPClient '{self._config.name}' is not connected. Call connect() first.")
