# MassClaw Agent Protocol Layer — Design Spec

## Context

MassClaw agents currently communicate only through shared semantic memory and the orchestration scheduler. There's no way for external agents to connect via standard protocols, no agent-to-agent direct messaging, and no MCP integration. This spec designs a multi-protocol agent communication layer that makes MassClaw a true participant in the emerging AI agent ecosystem.

## Design Choices (User-Confirmed)

- **MCP: Both client and server** — MassClaw calls external MCP tool servers AND exposes itself as an MCP server
- **Agent-to-agent: Redis message bus** — Builds on existing EventBus pub/sub
- **Multi-protocol**: HTTP REST + MCP + WebSocket, with a protocol adapter layer

---

## Architecture Overview

```
External World                          MassClaw Core
=============                          =============

MCP Servers         ──MCP Client──┐
(Slack, GitHub,                    │
 databases, etc.)                  │
                                   ├──→  Protocol Adapter  ──→  Agent Registry
External AI Apps    ──MCP Server──┤          Layer                  │
(Claude Code,                      │           │                    │
 Cursor, etc.)                     │           ▼                    ▼
                                   ├──→  Message Router    ──→  Scheduler
HTTP Agents         ──REST API────┤          │
(OpenClaw,                         │           ▼
 custom agents)                    │      Message Bus
                                   │     (Redis Pub/Sub)
WebSocket Agents    ──WS────────┘          │
(persistent,                               ▼
 real-time)                        Agent-to-Agent Messaging
```

---

## Component 1: Protocol Adapter Layer

### `app/protocols/base.py` — Abstract Protocol Adapter

```python
class ProtocolType(str, Enum):
    HTTP = "http"
    MCP = "mcp"
    WEBSOCKET = "websocket"

@dataclass
class AgentMessage:
    message_id: str
    sender_id: str
    recipient_id: str | None  # None = broadcast
    channel: str              # e.g., "agent.direct.{id}" or "agent.capability.research"
    content: str
    message_type: str         # "request", "response", "notification"
    metadata: dict[str, Any]
    timestamp: str
    correlation_id: str | None  # Links request/response pairs

class ProtocolAdapter(ABC):
    protocol_type: ProtocolType

    @abstractmethod
    async def send_message(self, agent: Agent, message: AgentMessage) -> AgentMessage | None: ...

    @abstractmethod
    async def health_check(self, agent: Agent) -> bool: ...
```

### `app/protocols/http_adapter.py` — HTTP/REST Adapter
- Sends messages to agents via `httpx.AsyncClient.post(agent.endpoint, json=message)`
- Health check via `GET {agent.health_check_url}`
- Used for OpenClaw-compatible external agents

### `app/protocols/mcp_adapter.py` — MCP Adapter
- **Client mode**: Connects to MCP servers, discovers tools, calls them
- **Server mode**: Exposes MassClaw capabilities as MCP tools
- Uses `mcp` Python SDK (`pip install mcp`)

### `app/protocols/websocket_adapter.py` — WebSocket Adapter
- Maintains persistent connections to agents
- Bidirectional real-time messaging
- Heartbeat + reconnection logic
- Builds on existing `ConnectionManager` pattern

### `app/protocols/router.py` — Protocol Router
- Routes messages to the correct adapter based on agent's protocol type
- Agent model gets a new `protocol_type` field
- Fallback: if protocol_type not set, defaults to HTTP

---

## Component 2: MCP Client (MassClaw calls external MCP servers)

### How it works
1. Admin registers an MCP server via API: `POST /api/v1/mcp/servers` with `{name, command, args, env}`
2. MassClaw spawns the MCP server process via stdio transport
3. Discovers available tools via `mcp.list_tools()`
4. Each discovered tool is wrapped as a `MCPToolProvider(ToolProvider)` and registered in `ToolRegistry`
5. Agents can now use these MCP tools in their agentic loops — same as built-in tools

### `app/protocols/mcp_client.py`
```python
class MCPServerConfig:
    name: str
    command: str        # e.g., "npx"
    args: list[str]     # e.g., ["-y", "@modelcontextprotocol/server-github"]
    env: dict[str, str] # e.g., {"GITHUB_TOKEN": "..."}

class MCPClient:
    async def connect(self, config: MCPServerConfig) -> None
    async def list_tools(self) -> list[MCPToolSchema]
    async def call_tool(self, name: str, arguments: dict) -> MCPToolResult
    async def disconnect(self) -> None

class MCPToolProvider(ToolProvider):
    """Wraps an MCP server tool as a MassClaw ToolProvider."""
    execution_mode = ExecutionMode.IN_PROCESS  # MCP handles its own sandbox
    # execute() calls self.mcp_client.call_tool()
```

### `app/protocols/mcp_registry.py` — MCP Server Manager
- Manages lifecycle of connected MCP servers (connect, health, reconnect, disconnect)
- Persists server configs in database
- Auto-reconnects on failure
- Exposes status via API

---

## Component 3: MCP Server (External apps call MassClaw)

### How it works
1. MassClaw exposes an MCP-compatible server endpoint
2. External apps (Claude Code, Cursor, etc.) connect to it
3. MassClaw advertises tools like:
   - `submit_workflow` — submit a natural language task
   - `get_workflow_status` — check progress
   - `get_workflow_result` — get final result
   - `search_agents` — discover available agents
   - `query_memory` — semantic memory search
4. External apps call these tools via MCP protocol

### `app/protocols/mcp_server.py`
- Implements `mcp.Server` from the MCP SDK
- Registers MassClaw capabilities as MCP tools
- Handles incoming tool calls by delegating to existing services
- Runs as a separate process or integrated into FastAPI via SSE transport

---

## Component 4: Agent-to-Agent Message Bus

### Channel naming convention
```
massclaw:agent:direct:{agent_id}     — Direct messages to a specific agent
massclaw:agent:capability:{cap}      — Broadcast to agents with a capability
massclaw:agent:workflow:{workflow_id} — Messages within a workflow context
massclaw:agent:broadcast              — System-wide broadcast
```

### `app/protocols/message_bus.py`
```python
class AgentMessageBus:
    """Redis-backed agent-to-agent messaging built on EventBus."""

    async def send_direct(self, sender_id: str, recipient_id: str, content: str, **kwargs) -> AgentMessage
    async def broadcast_capability(self, sender_id: str, capability: str, content: str, **kwargs) -> AgentMessage
    async def subscribe_direct(self, agent_id: str) -> AsyncIterator[AgentMessage]
    async def subscribe_capability(self, capability: str) -> AsyncIterator[AgentMessage]
    async def request_response(self, sender_id: str, recipient_id: str, content: str, timeout: float = 30.0) -> AgentMessage
```

### Request-Response Pattern
- `request_response()` sends a message and waits for a correlated response
- Uses `correlation_id` to match request/response pairs
- Timeout enforcement (default 30s)
- Used by consensus service, delegation, and inter-agent queries

### Integration with Scheduler
- During task execution, agents can send messages to request help from other agents
- The scheduler tracks message costs (each message = small credit cost)
- Messages are logged to audit trail

---

## Component 5: External Agent Registration

### Enhanced Agent Registration
Extend `AgentCreate` schema with:
```python
protocol_type: ProtocolType = ProtocolType.HTTP  # http, mcp, websocket
mcp_config: MCPServerConfig | None = None        # For MCP agents
websocket_url: str | None = None                  # For WebSocket agents
```

### Agent model changes
Add to `Agent` model:
```python
protocol_type: Mapped[str] = mapped_column(String(20), default="http")
```

### Alembic migration needed for `protocol_type` column

---

## API Endpoints

### MCP Server Management
- `POST /api/v1/mcp/servers` — Register an MCP server
- `GET /api/v1/mcp/servers` — List connected MCP servers
- `GET /api/v1/mcp/servers/{name}` — Get server details + discovered tools
- `DELETE /api/v1/mcp/servers/{name}` — Disconnect and remove
- `POST /api/v1/mcp/servers/{name}/reconnect` — Force reconnect

### Agent Messaging
- `POST /api/v1/agents/{id}/messages` — Send a message to an agent
- `GET /api/v1/agents/{id}/messages` — Get message history
- `GET /api/v1/agents/{id}/messages/stream` — SSE stream of incoming messages

---

## File Structure

```
app/protocols/
    __init__.py
    base.py              — ProtocolType, AgentMessage, ProtocolAdapter ABC
    router.py            — ProtocolRouter (routes to correct adapter)
    http_adapter.py      — HTTP/REST adapter
    mcp_adapter.py       — MCP protocol adapter
    mcp_client.py        — MCP client (call external servers)
    mcp_server.py        — MCP server (expose MassClaw as MCP)
    mcp_registry.py      — MCP server lifecycle manager
    websocket_adapter.py — WebSocket persistent connections
    message_bus.py       — Agent-to-agent Redis pub/sub messaging
app/api/
    mcp.py               — MCP server management endpoints
    (modify agents.py)   — Add messaging endpoints
```

---

## Configuration

```python
# Agent Protocol
mcp_server_enabled: bool = True
mcp_client_enabled: bool = True
agent_message_ttl_seconds: int = 3600
agent_message_max_size_bytes: int = 100000
websocket_agent_heartbeat_seconds: int = 30
websocket_agent_reconnect_delay_seconds: int = 5
```

---

## Implementation Phases

### Phase 1: Foundation + Message Bus
1. `app/protocols/base.py` — ProtocolType, AgentMessage, ProtocolAdapter
2. `app/protocols/message_bus.py` — AgentMessageBus on Redis
3. Agent model: add `protocol_type` column + migration
4. Config settings
5. Tests

### Phase 2: HTTP + WebSocket Adapters
6. `app/protocols/http_adapter.py`
7. `app/protocols/websocket_adapter.py`
8. `app/protocols/router.py` — ProtocolRouter
9. Tests

### Phase 3: MCP Client
10. `app/protocols/mcp_client.py` — MCPClient + MCPToolProvider
11. `app/protocols/mcp_registry.py` — MCP server lifecycle
12. `app/api/mcp.py` — MCP management endpoints
13. Tests

### Phase 4: MCP Server
14. `app/protocols/mcp_server.py` — Expose MassClaw as MCP server
15. Integration with existing services
16. Tests

### Phase 5: Integration
17. Wire message bus into scheduler for inter-agent communication
18. Add messaging endpoints to agents API
19. Update agent registration for protocol_type
20. Full integration tests

## Verification

1. `GET /api/v1/mcp/servers` — lists connected MCP servers
2. Register an MCP server (e.g., filesystem), verify tools appear in `GET /api/v1/tools`
3. Send agent-to-agent message, verify delivery via message bus
4. External app connects to MassClaw MCP server, submits workflow
5. WebSocket agent connects, receives real-time messages
6. All existing tests still pass
