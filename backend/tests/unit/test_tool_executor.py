from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult
from app.tools.executor import ToolExecutor


class FakeTool(ToolProvider):
    name = "fake_tool"
    description = "A fake tool for testing"
    parameters_schema = {"type": "object", "properties": {"input": {"type": "string"}}}
    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities = {"research"}
    estimated_cost_credits = 1.0

    async def execute(self, arguments, context):
        return ToolResult(content=f"Result: {arguments.get('input', '')}", success=True)


@pytest.fixture
def tool_context():
    return ToolContext(workflow_id=uuid.uuid4(), agent_id=uuid.uuid4(), workspace_path="/tmp/test")


@pytest.fixture
def executor():
    return ToolExecutor(session=AsyncMock(), redis=AsyncMock())


class TestToolExecutor:
    @pytest.mark.asyncio
    async def test_execute_success(self, executor, tool_context):
        with patch("app.tools.executor.get_tool_registry") as mock_reg:
            mock_reg.return_value.get.return_value = FakeTool()
            result = await executor.execute_tool("fake_tool", {"input": "hello"}, tool_context)
            assert result.success is True
            assert "hello" in result.content

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self, executor, tool_context):
        with patch("app.tools.executor.get_tool_registry") as mock_reg:
            mock_reg.return_value.get.return_value = None
            result = await executor.execute_tool("nonexistent", {}, tool_context)
            assert result.success is False
            assert "not found" in result.content.lower()

    @pytest.mark.asyncio
    async def test_cost_tracking(self, executor, tool_context):
        with patch("app.tools.executor.get_tool_registry") as mock_reg:
            mock_reg.return_value.get.return_value = FakeTool()
            result = await executor.execute_tool("fake_tool", {"input": "test"}, tool_context)
            assert result.cost_credits == 1.0
