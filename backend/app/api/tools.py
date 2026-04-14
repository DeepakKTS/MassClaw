from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.tools.registry import get_tool_registry

router = APIRouter()


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
