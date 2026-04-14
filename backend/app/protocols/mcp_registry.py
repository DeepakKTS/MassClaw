"""MCP server manager — lifecycle management for MCP server connections."""

from __future__ import annotations

from app.core.logging import get_logger
from app.protocols.mcp_client import MCPClient, MCPServerConfig
from app.protocols.mcp_tool_provider import MCPToolProvider

logger = get_logger(__name__)

_mcp_manager: MCPServerManager | None = None


class MCPServerManager:
    """Manages a collection of MCP server configurations and their clients.

    Responsibilities:
    - Store server configs
    - Connect/disconnect clients on demand
    - Auto-discover tools on connect and register them in the ToolRegistry
    """

    def __init__(self) -> None:
        self._configs: dict[str, MCPServerConfig] = {}
        self._clients: dict[str, MCPClient] = {}

    # ------------------------------------------------------------------
    # Config CRUD
    # ------------------------------------------------------------------

    def add_config(self, config: MCPServerConfig) -> None:
        """Add or replace a server configuration."""
        self._configs[config.name] = config
        logger.debug("mcp_manager_config_added", server=config.name)

    def get_config(self, name: str) -> MCPServerConfig | None:
        """Return the config for *name*, or None if not registered."""
        return self._configs.get(name)

    def remove_config(self, name: str) -> None:
        """Remove the configuration for *name*.

        If a client is currently connected, it should be disconnected first
        via :meth:`disconnect`.
        """
        self._configs.pop(name, None)
        logger.debug("mcp_manager_config_removed", server=name)

    def list_servers(self) -> list[str]:
        """Return names of all registered server configs."""
        return list(self._configs.keys())

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, name: str) -> MCPClient:
        """Connect to an MCP server, discover its tools, and register them.

        Raises KeyError if *name* is not a registered config.
        Raises RuntimeError (from MCPClient) on connection failure.
        """
        if name not in self._configs:
            raise KeyError(f"No MCP server config registered for '{name}'")

        # Reuse existing connected client
        if name in self._clients and self._clients[name].is_connected:
            return self._clients[name]

        config = self._configs[name]
        client = MCPClient(config)
        await client.connect()
        self._clients[name] = client

        # Discover tools and register them
        await self._register_tools(client)

        return client

    async def disconnect(self, name: str) -> None:
        """Disconnect the client for *name* (no-op if not connected)."""
        client = self._clients.pop(name, None)
        if client is not None:
            await client.disconnect()

    async def disconnect_all(self) -> None:
        """Disconnect all active MCP clients."""
        names = list(self._clients.keys())
        for name in names:
            await self.disconnect(name)

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    async def _register_tools(self, client: MCPClient) -> None:
        """Discover tools from *client* and register them in the ToolRegistry."""
        from app.tools.registry import get_tool_registry

        try:
            tools = await client.list_tools()
            registry = get_tool_registry()
            for tool_schema in tools:
                provider = MCPToolProvider(
                    mcp_client=client,
                    tool_name=tool_schema["name"],
                    description=tool_schema.get("description", ""),
                    parameters_schema=tool_schema.get("inputSchema", {}),
                )
                registry.register(provider)
                logger.info(
                    "mcp_tool_registered",
                    tool=provider.name,
                    server=client.config.name,
                )
        except Exception as exc:
            logger.warning(
                "mcp_tool_registration_failed",
                server=client.config.name,
                error=str(exc),
            )


def get_mcp_manager() -> MCPServerManager:
    """Return the module-level MCPServerManager singleton.

    Creates it on first call.
    """
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPServerManager()
    return _mcp_manager
