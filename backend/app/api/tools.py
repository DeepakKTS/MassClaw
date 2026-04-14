from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.tools.registry import get_tool_registry

router = APIRouter()


class CodeRunRequest(BaseModel):
    language: str = Field(..., description="python, javascript, or shell")
    code: str = Field(..., min_length=1, max_length=50000)
    timeout: int = Field(default=30, ge=1, le=60)


@router.get("")
async def list_tools() -> list[dict[str, Any]]:
    """List all available tools with their schemas."""
    registry = get_tool_registry()
    return [
        {
            "name": t.name,
            "description": t.description,
            "execution_mode": t.execution_mode.value,
            "required_capabilities": sorted(t.required_capabilities),
            "estimated_cost_credits": t.estimated_cost_credits,
            "parameters": t.parameters_schema,
        }
        for t in registry.list_tools()
    ]


@router.post("/code_execute/run")
async def run_code(data: CodeRunRequest) -> dict[str, Any]:
    """Execute code directly for testing. Returns stdout/stderr."""
    import uuid

    from app.config import get_settings
    from app.tools.base import ToolContext
    from app.tools.code_execution import CodeExecutionTool

    settings = get_settings()
    tool = CodeExecutionTool()
    ctx = ToolContext(
        workflow_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        workspace_path=f"{settings.tool_workspace_base}/playground",
        timeout_seconds=data.timeout,
    )
    result = await tool.execute(
        {"language": data.language, "code": data.code},
        ctx,
    )
    return {
        "success": result.success,
        "output": result.content,
        "metadata": result.metadata,
    }


@router.get("/{tool_name}")
async def get_tool(tool_name: str) -> dict[str, Any]:
    """Get detailed information about a specific tool."""
    registry = get_tool_registry()
    tool = registry.get(tool_name)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    return {
        "name": tool.name,
        "description": tool.description,
        "execution_mode": tool.execution_mode.value,
        "required_capabilities": sorted(tool.required_capabilities),
        "estimated_cost_credits": tool.estimated_cost_credits,
        "parameters": tool.parameters_schema,
        "schema": tool.to_schema(),
    }
