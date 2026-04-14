"""Unit tests for MassClawMCPServer."""

from __future__ import annotations

import pytest

from app.protocols.mcp_server import MassClawMCPServer


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------

def test_create_server():
    """MassClawMCPServer can be instantiated without errors."""
    server = MassClawMCPServer()
    assert server is not None


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------

_EXPECTED_TOOLS = {
    "submit_workflow",
    "get_workflow_status",
    "get_workflow_result",
    "search_agents",
    "query_memory",
}


def test_tools_defined():
    """MassClawMCPServer exposes exactly 5 tool names."""
    server = MassClawMCPServer()
    tools = server.list_tools()

    assert len(tools) == 5
    names = {t["name"] for t in tools}
    assert names == _EXPECTED_TOOLS


def test_tool_schemas():
    """Every tool has name, description, and inputSchema fields."""
    server = MassClawMCPServer()
    for tool in server.list_tools():
        assert "name" in tool, f"Tool missing 'name': {tool}"
        assert "description" in tool, f"Tool '{tool['name']}' missing 'description'"
        assert "inputSchema" in tool, f"Tool '{tool['name']}' missing 'inputSchema'"

        schema = tool["inputSchema"]
        assert isinstance(schema, dict), f"Tool '{tool['name']}' inputSchema must be a dict"
        assert schema.get("type") == "object", (
            f"Tool '{tool['name']}' inputSchema type should be 'object'"
        )
        assert "properties" in schema, (
            f"Tool '{tool['name']}' inputSchema missing 'properties'"
        )


def test_tool_schema_required_fields():
    """Each tool's inputSchema declares the expected required fields."""
    server = MassClawMCPServer()
    tools = {t["name"]: t for t in server.list_tools()}

    assert "prompt" in tools["submit_workflow"]["inputSchema"]["required"]
    assert "budget_limit" in tools["submit_workflow"]["inputSchema"]["required"]

    assert "workflow_id" in tools["get_workflow_status"]["inputSchema"]["required"]
    assert "workflow_id" in tools["get_workflow_result"]["inputSchema"]["required"]

    assert "capabilities" in tools["search_agents"]["inputSchema"]["required"]
    assert "query" in tools["query_memory"]["inputSchema"]["required"]


@pytest.mark.asyncio
async def test_handle_tool_call_unknown_raises():
    """handle_tool_call raises ValueError for unknown tool names."""
    server = MassClawMCPServer()
    with pytest.raises(ValueError, match="Unknown MCP tool"):
        await server.handle_tool_call("nonexistent_tool", {})
