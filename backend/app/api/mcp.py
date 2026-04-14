from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.protocols.mcp_registry import get_mcp_manager

router = APIRouter()


class MCPServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    command: str = Field(..., min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


@router.get("/servers")
async def list_mcp_servers() -> list[dict[str, Any]]:
    """List all configured MCP servers."""
    mgr = get_mcp_manager()
    return [{"name": name} for name in mgr.list_servers()]


@router.post("/servers", status_code=201)
async def register_mcp_server(data: MCPServerCreate) -> dict[str, Any]:
    """Register and connect to an MCP server."""
    from app.protocols.mcp_client import MCPServerConfig

    mgr = get_mcp_manager()
    config = MCPServerConfig(name=data.name, command=data.command, args=data.args, env=data.env)
    mgr.add_config(config)
    try:
        client = await mgr.connect(data.name)
        tools = await client.list_tools()
        return {"name": data.name, "status": "connected", "tools_registered": len(tools)}
    except Exception as e:
        mgr.remove_config(data.name)
        raise HTTPException(status_code=500, detail=f"Failed to connect: {e}") from e


@router.get("/servers/{name}")
async def get_mcp_server(name: str) -> dict[str, Any]:
    """Get details of a specific MCP server."""
    mgr = get_mcp_manager()
    config = mgr.get_config(name)
    if config is None:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")
    return {"name": name, "connected": name in mgr._clients and mgr._clients[name].is_connected}


@router.delete("/servers/{name}")
async def remove_mcp_server(name: str) -> dict[str, str]:
    """Disconnect and remove an MCP server."""
    mgr = get_mcp_manager()
    await mgr.disconnect(name)
    mgr.remove_config(name)
    return {"status": "removed", "name": name}


@router.post("/servers/{name}/reconnect")
async def reconnect_mcp_server(name: str) -> dict[str, Any]:
    """Force reconnect to an MCP server."""
    mgr = get_mcp_manager()
    if mgr.get_config(name) is None:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")
    await mgr.disconnect(name)
    try:
        client = await mgr.connect(name)
        tools = await client.list_tools()
        return {"name": name, "status": "reconnected", "tools_registered": len(tools)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reconnect failed: {e}") from e
