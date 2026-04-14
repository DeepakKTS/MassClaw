from __future__ import annotations

import uuid

import pytest

from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult


class TestToolResult:
    def test_create_success_result(self):
        result = ToolResult(content="Hello", success=True, metadata={}, artifacts=[])
        assert result.success is True
        assert result.content == "Hello"
        assert result.cost_credits == 0.0

    def test_create_failure_result(self):
        result = ToolResult(content="Error", success=False, metadata={"error": "timeout"}, artifacts=[])
        assert result.success is False


class TestToolContext:
    def test_create_context(self):
        ctx = ToolContext(
            workflow_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            workspace_path="/tmp/test",
        )
        assert ctx.timeout_seconds == 30
        assert ctx.max_iterations == 5


class TestToolProvider:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            ToolProvider()  # type: ignore[abstract]

    def test_to_schema(self):
        class FakeTool(ToolProvider):
            name = "fake"
            description = "A fake tool"
            parameters_schema = {"type": "object", "properties": {"q": {"type": "string"}}}
            execution_mode = ExecutionMode.IN_PROCESS
            required_capabilities = {"research"}
            estimated_cost_credits = 0.1

            async def execute(self, arguments, context):
                return ToolResult(content="ok", success=True, metadata={}, artifacts=[])

        tool = FakeTool()
        schema = tool.to_schema()
        assert schema["name"] == "fake"
        assert schema["description"] == "A fake tool"
        assert "properties" in schema["input_schema"]
