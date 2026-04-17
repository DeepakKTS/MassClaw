"""Unit tests for MCPClient, MCPToolProvider, and MCPServerManager."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.protocols.mcp_client import MCPClient, MCPServerConfig
from app.protocols.mcp_registry import MCPServerManager
from app.protocols.mcp_tool_provider import MCPToolProvider
from app.tools.base import ExecutionMode, ToolContext


# ---------------------------------------------------------------------------
# MCPServerConfig
# ---------------------------------------------------------------------------


def test_mcp_server_config():
    """MCPServerConfig stores all fields correctly."""
    config = MCPServerConfig(
        name="my-server",
        command="python",
        args=["-m", "my_mcp_server"],
        env={"API_KEY": "secret"},
    )
    assert config.name == "my-server"
    assert config.command == "python"
    assert config.args == ["-m", "my_mcp_server"]
    assert config.env == {"API_KEY": "secret"}


def test_mcp_server_config_defaults():
    """MCPServerConfig defaults args and env to empty collections."""
    config = MCPServerConfig(name="minimal", command="/usr/bin/node")
    assert config.args == []
    assert config.env == {}


# ---------------------------------------------------------------------------
# MCPToolProvider
# ---------------------------------------------------------------------------


def _make_mcp_client(server_name: str = "test-server") -> MagicMock:
    client = MagicMock(spec=MCPClient)
    client.config = MCPServerConfig(name=server_name, command="node")
    client.is_connected = True
    return client


def _make_context() -> ToolContext:
    return ToolContext(
        workflow_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        workspace_path="/tmp/test",
    )


def test_mcp_tool_provider_properties():
    """MCPToolProvider has the correct name, description, and execution_mode."""
    client = _make_mcp_client("weather-server")
    provider = MCPToolProvider(
        mcp_client=client,
        tool_name="get_forecast",
        description="Get weather forecast",
        parameters_schema={"type": "object", "properties": {}},
    )

    assert provider.name == "mcp:weather-server:get_forecast"
    # When a description is explicitly supplied it is used verbatim
    assert provider.description == "Get weather forecast"
    assert provider.execution_mode == ExecutionMode.IN_PROCESS
    assert provider.required_capabilities == set()


def test_mcp_tool_provider_default_description():
    """MCPToolProvider auto-generates a description when none is given."""
    client = _make_mcp_client("weather-server")
    provider = MCPToolProvider(mcp_client=client, tool_name="get_forecast")

    # Auto-generated description must mention both server and tool name
    assert "weather-server" in provider.description
    assert "get_forecast" in provider.description


@pytest.mark.asyncio
async def test_mcp_tool_provider_execute():
    """MCPToolProvider.execute calls mcp_client.call_tool and returns a ToolResult."""
    client = _make_mcp_client("data-server")

    # Build a mock MCP CallToolResult
    mock_content_item = MagicMock()
    mock_content_item.text = "The answer is 42"
    mock_result = MagicMock()
    mock_result.isError = False
    mock_result.content = [mock_content_item]

    client.call_tool = AsyncMock(return_value=mock_result)

    provider = MCPToolProvider(
        mcp_client=client,
        tool_name="compute",
        description="Perform computation",
    )
    ctx = _make_context()

    result = await provider.execute({"input": "6 * 7"}, ctx)

    client.call_tool.assert_called_once_with("compute", {"input": "6 * 7"})
    assert result.success is True
    assert "42" in result.content
    assert result.metadata["server"] == "data-server"
    assert result.metadata["tool"] == "compute"


@pytest.mark.asyncio
async def test_mcp_tool_provider_execute_error():
    """MCPToolProvider.execute returns a failed ToolResult when isError is True."""
    client = _make_mcp_client("data-server")

    mock_content_item = MagicMock()
    mock_content_item.text = "Something went wrong"
    mock_result = MagicMock()
    mock_result.isError = True
    mock_result.content = [mock_content_item]

    client.call_tool = AsyncMock(return_value=mock_result)

    provider = MCPToolProvider(mcp_client=client, tool_name="risky_op")
    ctx = _make_context()

    result = await provider.execute({}, ctx)

    assert result.success is False
    assert "wrong" in result.content


# ---------------------------------------------------------------------------
# MCPServerManager — CRUD
# ---------------------------------------------------------------------------


def test_mcp_server_manager_crud():
    """MCPServerManager supports add/get/remove/list operations on configs."""
    manager = MCPServerManager()

    cfg_a = MCPServerConfig(name="server-a", command="python", args=["-m", "a"])
    cfg_b = MCPServerConfig(name="server-b", command="node", args=["server.js"])

    # Initially empty
    assert manager.list_servers() == []

    # Add
    manager.add_config(cfg_a)
    manager.add_config(cfg_b)
    assert set(manager.list_servers()) == {"server-a", "server-b"}

    # Get
    assert manager.get_config("server-a") is cfg_a
    assert manager.get_config("server-b") is cfg_b
    assert manager.get_config("nonexistent") is None

    # Remove
    manager.remove_config("server-a")
    assert manager.list_servers() == ["server-b"]
    assert manager.get_config("server-a") is None


def test_mcp_server_manager_overwrite_config():
    """Adding a config with the same name replaces the previous one."""
    manager = MCPServerManager()
    cfg1 = MCPServerConfig(name="srv", command="v1")
    cfg2 = MCPServerConfig(name="srv", command="v2")

    manager.add_config(cfg1)
    manager.add_config(cfg2)

    assert manager.get_config("srv") is cfg2
    assert len(manager.list_servers()) == 1
