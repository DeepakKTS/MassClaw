# Agent Protocol Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give MassClaw multi-protocol agent communication — MCP client/server, agent-to-agent messaging via Redis pub/sub, and HTTP/WebSocket protocol adapters — making it a full participant in the AI agent ecosystem.

**Architecture:** Protocol adapter layer abstracts HTTP/MCP/WebSocket behind a unified interface. MCP client wraps external tool servers as ToolProviders. MCP server exposes MassClaw as callable tools. Agent message bus extends existing EventBus for direct and broadcast messaging with request-response patterns.

**Tech Stack:** mcp Python SDK, existing Redis pub/sub EventBus, httpx, websockets, existing ToolProvider interface

**Spec:** `docs/superpowers/specs/2026-04-14-agent-protocol-layer-design.md`

---

## File Structure

```
NEW FILES:
  backend/app/protocols/__init__.py            — Package
  backend/app/protocols/base.py                — ProtocolType, AgentMessage, ProtocolAdapter ABC
  backend/app/protocols/message_bus.py         — AgentMessageBus (Redis pub/sub agent messaging)
  backend/app/protocols/http_adapter.py        — HTTP/REST protocol adapter
  backend/app/protocols/websocket_adapter.py   — WebSocket persistent connection adapter
  backend/app/protocols/router.py              — ProtocolRouter (routes to correct adapter)
  backend/app/protocols/mcp_client.py          — MCP client (connect to external MCP servers)
  backend/app/protocols/mcp_tool_provider.py   — MCPToolProvider (wraps MCP tools as ToolProviders)
  backend/app/protocols/mcp_server.py          — MCP server (expose MassClaw as MCP tools)
  backend/app/protocols/mcp_registry.py        — MCP server lifecycle manager
  backend/app/api/mcp.py                       — MCP management API endpoints
  backend/tests/unit/test_protocol_base.py     — Tests for base types
  backend/tests/unit/test_message_bus.py       — Tests for agent messaging
  backend/tests/unit/test_protocol_adapters.py — Tests for HTTP/WS adapters
  backend/tests/unit/test_mcp_client.py        — Tests for MCP client
  backend/tests/unit/test_mcp_server.py        — Tests for MCP server

MODIFIED FILES:
  backend/app/config.py                        — Protocol settings
  backend/app/models/agent.py                  — Add protocol_type field
  backend/app/schemas/agent.py                 — Add protocol_type to AgentCreate
  backend/app/api/router.py                    — Mount /mcp router
  backend/app/main.py                          — Init MCP registry in lifespan
  backend/pyproject.toml                       — Add mcp dependency
  backend/.env.example (root)                  — Document protocol settings
  backend/alembic/versions/                    — Migration for protocol_type column
```

---

### Task 1: Foundation — Protocol Base Types + Config

**Files:**
- Create: `backend/app/protocols/__init__.py`
- Create: `backend/app/protocols/base.py`
- Modify: `backend/app/config.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/unit/test_protocol_base.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/unit/test_protocol_base.py`:

```python
from __future__ import annotations

import pytest

from app.protocols.base import AgentMessage, ProtocolAdapter, ProtocolType


class TestProtocolType:
    def test_enum_values(self):
        assert ProtocolType.HTTP == "http"
        assert ProtocolType.MCP == "mcp"
        assert ProtocolType.WEBSOCKET == "websocket"


class TestAgentMessage:
    def test_create_message(self):
        msg = AgentMessage(
            sender_id="agent-a",
            recipient_id="agent-b",
            channel="agent.direct.agent-b",
            content="Hello",
            message_type="request",
        )
        assert msg.sender_id == "agent-a"
        assert msg.message_id  # auto-generated
        assert msg.timestamp  # auto-generated

    def test_broadcast_message(self):
        msg = AgentMessage(
            sender_id="agent-a",
            recipient_id=None,
            channel="agent.capability.research",
            content="Need help",
            message_type="notification",
        )
        assert msg.recipient_id is None


class TestProtocolAdapter:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            ProtocolAdapter()  # type: ignore[abstract]
```

- [ ] **Step 2: Run test — verify fail**

Run: `cd backend && pytest tests/unit/test_protocol_base.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create package and base module**

Create `backend/app/protocols/__init__.py`:
```python
"""MassClaw Agent Protocol Layer."""
```

Create `backend/app/protocols/base.py`:
```python
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.models.agent import Agent


class ProtocolType(str, enum.Enum):
    HTTP = "http"
    MCP = "mcp"
    WEBSOCKET = "websocket"


@dataclass
class AgentMessage:
    sender_id: str
    channel: str
    content: str
    message_type: str  # "request", "response", "notification"
    recipient_id: str | None = None
    message_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    correlation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ProtocolAdapter(ABC):
    """Abstract base class for protocol adapters."""

    protocol_type: ProtocolType

    @abstractmethod
    async def send_message(self, agent: Agent, message: AgentMessage) -> AgentMessage | None:
        """Send a message to an agent via this protocol. Returns response if synchronous."""

    @abstractmethod
    async def health_check(self, agent: Agent) -> bool:
        """Check if an agent is reachable via this protocol."""
```

- [ ] **Step 4: Add protocol config settings**

Add to `backend/app/config.py` after `tool_docker_image` line:

```python
    # Agent Protocol
    mcp_server_enabled: bool = True
    mcp_client_enabled: bool = True
    agent_message_ttl_seconds: int = 3600
    agent_message_max_size_bytes: int = 100000
    websocket_agent_heartbeat_seconds: int = 30
    websocket_agent_reconnect_delay_seconds: int = 5
```

- [ ] **Step 5: Add mcp dependency to pyproject.toml**

Add to `backend/pyproject.toml` dependencies after `beautifulsoup4`:

```toml
    # Agent Protocol
    "mcp>=1.0,<2.0",
```

Run: `cd backend && pip install ".[dev]"`

- [ ] **Step 6: Run test — verify pass**

Run: `cd backend && pytest tests/unit/test_protocol_base.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add app/protocols/ app/config.py pyproject.toml tests/unit/test_protocol_base.py
git commit -m "feat(protocols): add protocol base types, config, and mcp dependency

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Agent Message Bus

**Files:**
- Create: `backend/app/protocols/message_bus.py`
- Test: `backend/tests/unit/test_message_bus.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/unit/test_message_bus.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from app.protocols.base import AgentMessage
from app.protocols.message_bus import AgentMessageBus


@pytest.fixture
def bus(redis_client):
    return AgentMessageBus(redis=redis_client)


class TestAgentMessageBus:
    @pytest.mark.asyncio
    async def test_send_and_receive_direct(self, bus):
        received = []

        async def subscriber():
            async for msg in bus.subscribe_direct("agent-b"):
                received.append(msg)
                break  # Just get one message

        task = asyncio.create_task(subscriber())
        await asyncio.sleep(0.1)  # Let subscriber connect

        await bus.send_direct(
            sender_id="agent-a",
            recipient_id="agent-b",
            content="Hello agent B",
        )
        await asyncio.wait_for(task, timeout=2.0)
        assert len(received) == 1
        assert received[0].content == "Hello agent B"
        assert received[0].sender_id == "agent-a"

    @pytest.mark.asyncio
    async def test_send_broadcast_capability(self, bus):
        received = []

        async def subscriber():
            async for msg in bus.subscribe_capability("research"):
                received.append(msg)
                break

        task = asyncio.create_task(subscriber())
        await asyncio.sleep(0.1)

        await bus.broadcast_capability(
            sender_id="agent-a",
            capability="research",
            content="Need research help",
        )
        await asyncio.wait_for(task, timeout=2.0)
        assert len(received) == 1
        assert "research" in received[0].channel

    @pytest.mark.asyncio
    async def test_request_response(self, bus):
        async def responder():
            async for msg in bus.subscribe_direct("agent-b"):
                await bus.send_direct(
                    sender_id="agent-b",
                    recipient_id=msg.sender_id,
                    content=f"Reply to: {msg.content}",
                    correlation_id=msg.message_id,
                )
                break

        task = asyncio.create_task(responder())
        await asyncio.sleep(0.1)

        response = await bus.request_response(
            sender_id="agent-a",
            recipient_id="agent-b",
            content="What is 2+2?",
            timeout=2.0,
        )
        await task
        assert response is not None
        assert "Reply to:" in response.content
```

- [ ] **Step 2: Implement message_bus.py**

Create `backend/app/protocols/message_bus.py`:

```python
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis

from app.core.logging import get_logger
from app.protocols.base import AgentMessage

logger = get_logger(__name__)

CHANNEL_PREFIX = "massclaw:agent"


class AgentMessageBus:
    """Redis-backed agent-to-agent messaging."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self.redis = redis

    def _channel(self, *parts: str) -> str:
        return ":".join([CHANNEL_PREFIX, *parts])

    def _serialize(self, msg: AgentMessage) -> str:
        from dataclasses import asdict

        return json.dumps(asdict(msg))

    def _deserialize(self, raw: str | bytes) -> AgentMessage:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        return AgentMessage(**data)

    async def send_direct(
        self,
        sender_id: str,
        recipient_id: str,
        content: str,
        correlation_id: str | None = None,
        **kwargs: Any,
    ) -> AgentMessage:
        msg = AgentMessage(
            sender_id=sender_id,
            recipient_id=recipient_id,
            channel=self._channel("direct", recipient_id),
            content=content,
            message_type="request",
            correlation_id=correlation_id,
            metadata=kwargs,
        )
        await self.redis.publish(msg.channel, self._serialize(msg))
        logger.debug("agent_message_sent", sender=sender_id, recipient=recipient_id, channel=msg.channel)
        return msg

    async def broadcast_capability(
        self,
        sender_id: str,
        capability: str,
        content: str,
        **kwargs: Any,
    ) -> AgentMessage:
        msg = AgentMessage(
            sender_id=sender_id,
            recipient_id=None,
            channel=self._channel("capability", capability),
            content=content,
            message_type="notification",
            metadata=kwargs,
        )
        await self.redis.publish(msg.channel, self._serialize(msg))
        logger.debug("agent_broadcast_sent", sender=sender_id, capability=capability)
        return msg

    async def subscribe_direct(self, agent_id: str) -> AsyncIterator[AgentMessage]:
        channel = self._channel("direct", agent_id)
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield self._deserialize(message["data"])
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def subscribe_capability(self, capability: str) -> AsyncIterator[AgentMessage]:
        channel = self._channel("capability", capability)
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield self._deserialize(message["data"])
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def request_response(
        self,
        sender_id: str,
        recipient_id: str,
        content: str,
        timeout: float = 30.0,
    ) -> AgentMessage | None:
        sent = await self.send_direct(sender_id, recipient_id, content)
        correlation_id = sent.message_id

        async for msg in self.subscribe_direct(sender_id):
            if msg.correlation_id == correlation_id:
                return msg
            # Timeout handled by caller wrapping in asyncio.wait_for
        return None
```

- [ ] **Step 3: Run test — verify pass**

Run: `cd backend && pytest tests/unit/test_message_bus.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add app/protocols/message_bus.py tests/unit/test_message_bus.py
git commit -m "feat(protocols): add agent-to-agent message bus with direct and broadcast channels

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: HTTP + WebSocket Adapters + Protocol Router

**Files:**
- Create: `backend/app/protocols/http_adapter.py`
- Create: `backend/app/protocols/websocket_adapter.py`
- Create: `backend/app/protocols/router.py`
- Test: `backend/tests/unit/test_protocol_adapters.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/unit/test_protocol_adapters.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.protocols.base import AgentMessage, ProtocolType
from app.protocols.http_adapter import HTTPAdapter
from app.protocols.router import ProtocolRouter
from app.protocols.websocket_adapter import WebSocketAdapter


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.agent_id = "test-agent-id"
    agent.endpoint = "http://localhost:9000/agent"
    agent.health_check_url = "http://localhost:9000/health"
    agent.protocol_type = "http"
    return agent


class TestHTTPAdapter:
    @pytest.mark.asyncio
    async def test_send_message_success(self, mock_agent):
        adapter = HTTPAdapter()
        msg = AgentMessage(
            sender_id="sender", recipient_id="test-agent-id",
            channel="direct", content="hello", message_type="request",
        )
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"content": "response", "message_type": "response"}
        with patch("app.protocols.http_adapter.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.post = AsyncMock(return_value=mock_resp)
            result = await adapter.send_message(mock_agent, msg)
            assert result is not None

    @pytest.mark.asyncio
    async def test_health_check_success(self, mock_agent):
        adapter = HTTPAdapter()
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        with patch("app.protocols.http_adapter.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(return_value=mock_resp)
            assert await adapter.health_check(mock_agent) is True

    @pytest.mark.asyncio
    async def test_health_check_no_url(self, mock_agent):
        adapter = HTTPAdapter()
        mock_agent.health_check_url = None
        assert await adapter.health_check(mock_agent) is False


class TestProtocolRouter:
    @pytest.mark.asyncio
    async def test_routes_to_http(self, mock_agent):
        router = ProtocolRouter()
        msg = AgentMessage(
            sender_id="s", recipient_id="r", channel="c",
            content="test", message_type="request",
        )
        with patch.object(router._adapters[ProtocolType.HTTP], "send_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = None
            await router.send_message(mock_agent, msg)
            mock_send.assert_called_once()

    def test_get_adapter(self):
        router = ProtocolRouter()
        assert router.get_adapter(ProtocolType.HTTP) is not None
        assert router.get_adapter(ProtocolType.WEBSOCKET) is not None
```

- [ ] **Step 2: Implement http_adapter.py**

Create `backend/app/protocols/http_adapter.py`:

```python
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import httpx

from app.core.logging import get_logger
from app.models.agent import Agent
from app.protocols.base import AgentMessage, ProtocolAdapter, ProtocolType

logger = get_logger(__name__)


class HTTPAdapter(ProtocolAdapter):
    """HTTP/REST protocol adapter for external agents."""

    protocol_type = ProtocolType.HTTP

    async def send_message(self, agent: Agent, message: AgentMessage) -> AgentMessage | None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    agent.endpoint,
                    json=asdict(message),
                    headers={"Content-Type": "application/json", "X-MassClaw-Protocol": "http"},
                )
            if resp.status_code == 200:
                data: dict[str, Any] = resp.json()
                return AgentMessage(
                    sender_id=str(agent.agent_id),
                    recipient_id=message.sender_id,
                    channel=message.channel,
                    content=data.get("content", ""),
                    message_type=data.get("message_type", "response"),
                    correlation_id=message.message_id,
                    metadata=data.get("metadata", {}),
                )
            logger.warning("http_send_failed", agent=agent.name, status=resp.status_code)
            return None
        except Exception as e:
            logger.error("http_send_error", agent=agent.name, error=str(e))
            return None

    async def health_check(self, agent: Agent) -> bool:
        if not agent.health_check_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(agent.health_check_url)
            return 200 <= resp.status_code < 300
        except Exception:
            return False
```

- [ ] **Step 3: Implement websocket_adapter.py**

Create `backend/app/protocols/websocket_adapter.py`:

```python
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from app.config import get_settings
from app.core.logging import get_logger
from app.models.agent import Agent
from app.protocols.base import AgentMessage, ProtocolAdapter, ProtocolType

logger = get_logger(__name__)


class WebSocketAdapter(ProtocolAdapter):
    """WebSocket protocol adapter for persistent agent connections."""

    protocol_type = ProtocolType.WEBSOCKET

    def __init__(self) -> None:
        self._connections: dict[str, object] = {}  # agent_id -> websocket connection

    async def send_message(self, agent: Agent, message: AgentMessage) -> AgentMessage | None:
        agent_id = str(agent.agent_id)
        ws = self._connections.get(agent_id)

        if ws is None:
            ws = await self._connect(agent)
            if ws is None:
                return None

        try:
            import websockets

            await ws.send(json.dumps(asdict(message)))  # type: ignore[union-attr]
            settings = get_settings()
            raw = await asyncio.wait_for(
                ws.recv(),  # type: ignore[union-attr]
                timeout=settings.websocket_agent_heartbeat_seconds,
            )
            data = json.loads(raw)
            return AgentMessage(
                sender_id=agent_id,
                recipient_id=message.sender_id,
                channel=message.channel,
                content=data.get("content", ""),
                message_type="response",
                correlation_id=message.message_id,
                metadata=data.get("metadata", {}),
            )
        except Exception as e:
            logger.error("ws_send_error", agent=agent.name, error=str(e))
            self._connections.pop(agent_id, None)
            return None

    async def health_check(self, agent: Agent) -> bool:
        agent_id = str(agent.agent_id)
        ws = self._connections.get(agent_id)
        if ws is None:
            return False
        try:
            import websockets

            pong = await ws.ping()  # type: ignore[union-attr]
            await asyncio.wait_for(pong, timeout=5.0)
            return True
        except Exception:
            self._connections.pop(agent_id, None)
            return False

    async def _connect(self, agent: Agent) -> object | None:
        endpoint = getattr(agent, "endpoint", None) or getattr(agent, "metadata_", {}).get("websocket_url")
        if not endpoint:
            return None
        try:
            import websockets

            ws = await websockets.connect(endpoint)
            self._connections[str(agent.agent_id)] = ws
            logger.info("ws_agent_connected", agent=agent.name, endpoint=endpoint)
            return ws
        except Exception as e:
            logger.error("ws_connect_failed", agent=agent.name, error=str(e))
            return None

    async def disconnect(self, agent_id: str) -> None:
        ws = self._connections.pop(agent_id, None)
        if ws:
            try:
                await ws.close()  # type: ignore[union-attr]
            except Exception:
                pass
```

- [ ] **Step 4: Implement router.py**

Create `backend/app/protocols/router.py`:

```python
from __future__ import annotations

from app.core.logging import get_logger
from app.models.agent import Agent
from app.protocols.base import AgentMessage, ProtocolAdapter, ProtocolType
from app.protocols.http_adapter import HTTPAdapter
from app.protocols.websocket_adapter import WebSocketAdapter

logger = get_logger(__name__)


class ProtocolRouter:
    """Routes messages to agents via the correct protocol adapter."""

    def __init__(self) -> None:
        self._adapters: dict[ProtocolType, ProtocolAdapter] = {
            ProtocolType.HTTP: HTTPAdapter(),
            ProtocolType.WEBSOCKET: WebSocketAdapter(),
        }

    def get_adapter(self, protocol_type: ProtocolType) -> ProtocolAdapter | None:
        return self._adapters.get(protocol_type)

    def _resolve_protocol(self, agent: Agent) -> ProtocolType:
        proto = getattr(agent, "protocol_type", None)
        if proto and proto in ProtocolType.__members__.values():
            return ProtocolType(proto)
        return ProtocolType.HTTP  # Default

    async def send_message(self, agent: Agent, message: AgentMessage) -> AgentMessage | None:
        protocol = self._resolve_protocol(agent)
        adapter = self._adapters.get(protocol)
        if adapter is None:
            logger.error("no_adapter_for_protocol", protocol=protocol.value, agent=agent.name)
            return None
        return await adapter.send_message(agent, message)

    async def health_check(self, agent: Agent) -> bool:
        protocol = self._resolve_protocol(agent)
        adapter = self._adapters.get(protocol)
        if adapter is None:
            return False
        return await adapter.health_check(agent)
```

- [ ] **Step 5: Run tests — verify pass**

Run: `cd backend && pytest tests/unit/test_protocol_adapters.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add app/protocols/http_adapter.py app/protocols/websocket_adapter.py app/protocols/router.py tests/unit/test_protocol_adapters.py
git commit -m "feat(protocols): add HTTP/WebSocket adapters and ProtocolRouter

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: MCP Client + MCPToolProvider

**Files:**
- Create: `backend/app/protocols/mcp_client.py`
- Create: `backend/app/protocols/mcp_tool_provider.py`
- Create: `backend/app/protocols/mcp_registry.py`
- Test: `backend/tests/unit/test_mcp_client.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/unit/test_mcp_client.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.protocols.mcp_client import MCPServerConfig
from app.protocols.mcp_registry import MCPServerManager
from app.protocols.mcp_tool_provider import MCPToolProvider
from app.tools.base import ExecutionMode, ToolContext


class TestMCPServerConfig:
    def test_create_config(self):
        config = MCPServerConfig(
            name="test-server",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem"],
            env={"HOME": "/tmp"},
        )
        assert config.name == "test-server"
        assert config.command == "npx"


class TestMCPToolProvider:
    @pytest.mark.asyncio
    async def test_properties(self):
        mock_client = AsyncMock()
        tool = MCPToolProvider(
            tool_name="mcp_test_tool",
            tool_description="A test MCP tool",
            tool_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            mcp_client=mock_client,
            server_name="test-server",
        )
        assert tool.name == "mcp_test_tool"
        assert tool.execution_mode == ExecutionMode.IN_PROCESS
        schema = tool.to_schema()
        assert schema["name"] == "mcp_test_tool"

    @pytest.mark.asyncio
    async def test_execute_calls_client(self):
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = MagicMock(content=[MagicMock(text="result text", type="text")])
        tool = MCPToolProvider(
            tool_name="test", tool_description="test",
            tool_schema={}, mcp_client=mock_client, server_name="srv",
        )
        import uuid
        ctx = ToolContext(workflow_id=uuid.uuid4(), agent_id=uuid.uuid4(), workspace_path="/tmp")
        result = await tool.execute({"path": "/test"}, ctx)
        assert result.success is True
        assert "result text" in result.content
        mock_client.call_tool.assert_called_once()


class TestMCPServerManager:
    def test_create_manager(self):
        mgr = MCPServerManager()
        assert mgr.list_servers() == []

    def test_add_config(self):
        mgr = MCPServerManager()
        config = MCPServerConfig(name="test", command="echo", args=["hello"])
        mgr.add_config(config)
        assert len(mgr.list_servers()) == 1
        assert mgr.get_config("test") is not None

    def test_remove_config(self):
        mgr = MCPServerManager()
        config = MCPServerConfig(name="test", command="echo", args=["hello"])
        mgr.add_config(config)
        mgr.remove_config("test")
        assert mgr.list_servers() == []
```

- [ ] **Step 2: Implement mcp_client.py**

Create `backend/app/protocols/mcp_client.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


class MCPClient:
    """Connects to an external MCP server and provides tool access."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._session: Any = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            server_params = StdioServerParameters(
                command=self.config.command,
                args=self.config.args,
                env=self.config.env or None,
            )
            self._transport = stdio_client(server_params)
            self._read, self._write = await self._transport.__aenter__()
            self._session = ClientSession(self._read, self._write)
            await self._session.__aenter__()
            await self._session.initialize()
            self._connected = True
            logger.info("mcp_client_connected", server=self.config.name)
        except ImportError:
            logger.error("mcp_sdk_not_installed")
            raise RuntimeError("MCP SDK not installed. Install with: pip install mcp")
        except Exception as e:
            logger.error("mcp_connect_failed", server=self.config.name, error=str(e))
            raise

    async def list_tools(self) -> list[dict[str, Any]]:
        if not self._session:
            raise RuntimeError("Not connected")
        result = await self._session.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
            }
            for tool in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if not self._session:
            raise RuntimeError("Not connected")
        return await self._session.call_tool(name, arguments=arguments)

    async def disconnect(self) -> None:
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
                await self._transport.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None
            self._connected = False
            logger.info("mcp_client_disconnected", server=self.config.name)
```

- [ ] **Step 3: Implement mcp_tool_provider.py**

Create `backend/app/protocols/mcp_tool_provider.py`:

```python
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.protocols.mcp_client import MCPClient
from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult

logger = get_logger(__name__)


class MCPToolProvider(ToolProvider):
    """Wraps an MCP server tool as a MassClaw ToolProvider."""

    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities: set[str] = set()  # MCP tools available to all agents
    estimated_cost_credits = 0.5

    def __init__(
        self,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
        mcp_client: MCPClient,
        server_name: str,
    ) -> None:
        self.name = f"mcp:{server_name}:{tool_name}"
        self.description = f"[MCP:{server_name}] {tool_description}"
        self.parameters_schema = tool_schema
        self._mcp_client = mcp_client
        self._tool_name = tool_name
        self._server_name = server_name

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            result = await self._mcp_client.call_tool(self._tool_name, arguments)
            content_parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    content_parts.append(block.text)
                elif hasattr(block, "data"):
                    content_parts.append(f"[Binary data: {len(block.data)} bytes]")
            content = "\n".join(content_parts) if content_parts else "(no output)"
            return ToolResult(
                content=content,
                success=True,
                metadata={"mcp_server": self._server_name, "mcp_tool": self._tool_name},
            )
        except Exception as e:
            logger.error("mcp_tool_execution_failed", tool=self.name, error=str(e))
            return ToolResult(content=f"MCP tool execution failed: {e}", success=False)
```

- [ ] **Step 4: Implement mcp_registry.py**

Create `backend/app/protocols/mcp_registry.py`:

```python
from __future__ import annotations

from app.core.logging import get_logger
from app.protocols.mcp_client import MCPClient, MCPServerConfig
from app.protocols.mcp_tool_provider import MCPToolProvider
from app.tools.registry import get_tool_registry

logger = get_logger(__name__)

_manager: MCPServerManager | None = None


class MCPServerManager:
    """Manages lifecycle of connected MCP servers."""

    def __init__(self) -> None:
        self._configs: dict[str, MCPServerConfig] = {}
        self._clients: dict[str, MCPClient] = {}

    def add_config(self, config: MCPServerConfig) -> None:
        self._configs[config.name] = config

    def get_config(self, name: str) -> MCPServerConfig | None:
        return self._configs.get(name)

    def remove_config(self, name: str) -> None:
        self._configs.pop(name, None)
        self._clients.pop(name, None)

    def list_servers(self) -> list[dict]:
        return [
            {
                "name": cfg.name,
                "command": cfg.command,
                "connected": name in self._clients and self._clients[name].is_connected,
            }
            for name, cfg in self._configs.items()
        ]

    async def connect(self, name: str) -> list[str]:
        config = self._configs.get(name)
        if not config:
            raise ValueError(f"MCP server '{name}' not configured")

        client = MCPClient(config)
        await client.connect()
        self._clients[name] = client

        # Discover tools and register them
        tools = await client.list_tools()
        registry = get_tool_registry()
        registered_names = []
        for tool_info in tools:
            provider = MCPToolProvider(
                tool_name=tool_info["name"],
                tool_description=tool_info["description"],
                tool_schema=tool_info.get("schema", {}),
                mcp_client=client,
                server_name=name,
            )
            registry.register(provider)
            registered_names.append(provider.name)
            logger.info("mcp_tool_registered", tool=provider.name, server=name)

        return registered_names

    async def disconnect(self, name: str) -> None:
        client = self._clients.pop(name, None)
        if client:
            await client.disconnect()

    async def disconnect_all(self) -> None:
        for name in list(self._clients.keys()):
            await self.disconnect(name)


def get_mcp_manager() -> MCPServerManager:
    global _manager
    if _manager is None:
        _manager = MCPServerManager()
    return _manager
```

- [ ] **Step 5: Run tests — verify pass**

Run: `cd backend && pytest tests/unit/test_mcp_client.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add app/protocols/mcp_client.py app/protocols/mcp_tool_provider.py app/protocols/mcp_registry.py tests/unit/test_mcp_client.py
git commit -m "feat(protocols): add MCP client, tool provider wrapper, and server manager

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: MCP Server (Expose MassClaw as MCP)

**Files:**
- Create: `backend/app/protocols/mcp_server.py`
- Test: `backend/tests/unit/test_mcp_server.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/unit/test_mcp_server.py`:

```python
from __future__ import annotations

import pytest

from app.protocols.mcp_server import MassClawMCPServer


class TestMassClawMCPServer:
    def test_create_server(self):
        server = MassClawMCPServer()
        assert server is not None

    def test_tools_defined(self):
        server = MassClawMCPServer()
        tool_names = server.get_tool_names()
        assert "submit_workflow" in tool_names
        assert "get_workflow_status" in tool_names
        assert "get_workflow_result" in tool_names
        assert "search_agents" in tool_names
        assert "query_memory" in tool_names

    def test_tool_schemas(self):
        server = MassClawMCPServer()
        schemas = server.get_tool_schemas()
        for schema in schemas:
            assert "name" in schema
            assert "description" in schema
            assert "inputSchema" in schema
```

- [ ] **Step 2: Implement mcp_server.py**

Create `backend/app/protocols/mcp_server.py`:

```python
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


# MCP tool definitions for exposing MassClaw capabilities
_MCP_TOOLS = [
    {
        "name": "submit_workflow",
        "description": "Submit a natural language task to MassClaw for multi-agent execution. Returns a workflow ID for tracking.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "instruction": {"type": "string", "description": "What you want the agents to do"},
                "budget": {"type": "number", "description": "Maximum budget in credits", "default": 500},
                "domain": {"type": "string", "description": "Domain hint (auto-detected if omitted)"},
                "priority": {"type": "integer", "description": "Priority 1-10 (1=highest)", "default": 5},
            },
            "required": ["instruction"],
        },
    },
    {
        "name": "get_workflow_status",
        "description": "Check the progress and status of a submitted workflow.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "The workflow ID returned by submit_workflow"},
            },
            "required": ["workflow_id"],
        },
    },
    {
        "name": "get_workflow_result",
        "description": "Get the final result of a completed workflow.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "The workflow ID"},
            },
            "required": ["workflow_id"],
        },
    },
    {
        "name": "search_agents",
        "description": "Search for available agents by capability.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "capability": {"type": "string", "description": "Capability to search for (e.g., 'research', 'code-execution')"},
            },
            "required": ["capability"],
        },
    },
    {
        "name": "query_memory",
        "description": "Search MassClaw's semantic memory for relevant information.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Semantic search query"},
                "top_k": {"type": "integer", "description": "Number of results", "default": 5},
            },
            "required": ["query"],
        },
    },
]


class MassClawMCPServer:
    """Exposes MassClaw capabilities as MCP tools for external AI apps."""

    def __init__(self) -> None:
        self._tools = {t["name"]: t for t in _MCP_TOOLS}

    def get_tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return list(self._tools.values())

    def get_tool(self, name: str) -> dict[str, Any] | None:
        return self._tools.get(name)

    async def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle an incoming MCP tool call by delegating to MassClaw services."""
        handler = getattr(self, f"_handle_{name}", None)
        if handler is None:
            return {"error": f"Unknown tool: {name}"}
        try:
            return await handler(arguments)
        except Exception as e:
            logger.error("mcp_server_tool_error", tool=name, error=str(e))
            return {"error": str(e)}

    async def _handle_submit_workflow(self, args: dict[str, Any]) -> dict[str, Any]:
        from app.core.database import db_session_context
        from app.core.redis import get_redis_manager
        from app.schemas.workflow import WorkflowCreate
        from app.services.workflow_service import WorkflowService

        async with db_session_context() as session:
            redis = get_redis_manager().get_cache_client()
            svc = WorkflowService(session=session, redis=redis)
            data = WorkflowCreate(
                prompt=args["instruction"],
                budget_limit=args.get("budget", 500),
                domain=args.get("domain"),
                priority=args.get("priority", 5),
                user_id="mcp-external",
            )
            workflow = await svc.create_and_execute(data)
            return {
                "workflow_id": str(workflow.workflow_id),
                "status": workflow.status.value,
                "message": "Workflow submitted successfully",
            }

    async def _handle_get_workflow_status(self, args: dict[str, Any]) -> dict[str, Any]:
        import uuid

        from app.core.database import db_session_context
        from app.core.redis import get_redis_manager
        from app.services.workflow_service import WorkflowService

        async with db_session_context() as session:
            redis = get_redis_manager().get_cache_client()
            svc = WorkflowService(session=session, redis=redis)
            status = await svc.get_workflow_status(uuid.UUID(args["workflow_id"]))
            return status.model_dump()

    async def _handle_get_workflow_result(self, args: dict[str, Any]) -> dict[str, Any]:
        import uuid

        from app.core.database import db_session_context
        from app.core.redis import get_redis_manager
        from app.services.workflow_service import WorkflowService

        async with db_session_context() as session:
            redis = get_redis_manager().get_cache_client()
            svc = WorkflowService(session=session, redis=redis)
            workflow = await svc.get_workflow(uuid.UUID(args["workflow_id"]))
            return {
                "workflow_id": str(workflow.workflow_id),
                "status": workflow.status.value,
                "result": workflow.result or {},
            }

    async def _handle_search_agents(self, args: dict[str, Any]) -> dict[str, Any]:
        from app.core.database import db_session_context
        from app.core.redis import get_redis_manager
        from app.services.agent_service import AgentService

        async with db_session_context() as session:
            redis = get_redis_manager().get_cache_client()
            svc = AgentService(session=session, redis=redis)
            agents = await svc.search_by_capability(args["capability"])
            return {
                "agents": [
                    {"name": a.name, "capabilities": a.capabilities, "trust_score": a.trust_score}
                    for a in agents
                ]
            }

    async def _handle_query_memory(self, args: dict[str, Any]) -> dict[str, Any]:
        from app.core.database import db_session_context
        from app.core.redis import get_redis_manager
        from app.schemas.memory import MemoryQueryRequest
        from app.services.memory_service import MemoryService

        async with db_session_context() as session:
            redis = get_redis_manager().get_cache_client()
            svc = MemoryService(session=session, redis=redis)
            results = await svc.query_memory(
                MemoryQueryRequest(query=args["query"], top_k=args.get("top_k", 5))
            )
            return {"results": [{"content": r.content, "similarity": r.similarity} for r in results]}
```

- [ ] **Step 3: Run tests — verify pass**

Run: `cd backend && pytest tests/unit/test_mcp_server.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add app/protocols/mcp_server.py tests/unit/test_mcp_server.py
git commit -m "feat(protocols): add MCP server exposing MassClaw as 5 MCP tools

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Agent Model + Schema + Migration

**Files:**
- Modify: `backend/app/models/agent.py`
- Modify: `backend/app/schemas/agent.py`
- Create: Alembic migration

- [ ] **Step 1: Add protocol_type to Agent model**

Read `backend/app/models/agent.py`. Add after the `metadata_` field:

```python
    protocol_type: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        server_default=text("'http'"),
    )
```

- [ ] **Step 2: Add protocol_type to AgentCreate schema**

Read `backend/app/schemas/agent.py`. Add after `metadata` field in AgentCreate:

```python
    protocol_type: str | None = Field(default="http", description="Protocol: http, mcp, or websocket")
```

Also add to `AgentResponse` and `AgentSummary` if they exist.

- [ ] **Step 3: Create Alembic migration**

Run: `cd backend && alembic revision -m "add_agent_protocol_type"`

Edit the generated migration to add:
```python
def upgrade():
    op.add_column("agents", sa.Column("protocol_type", sa.String(20), server_default="http", nullable=True))

def downgrade():
    op.drop_column("agents", "protocol_type")
```

Run: `cd backend && alembic upgrade head`

- [ ] **Step 4: Verify**

Run: `cd backend && python -c "from app.models.agent import Agent; print('OK')"`

- [ ] **Step 5: Commit**

```bash
git add app/models/agent.py app/schemas/agent.py alembic/versions/
git commit -m "feat(protocols): add protocol_type field to Agent model and schema

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: API Endpoints + Integration

**Files:**
- Create: `backend/app/api/mcp.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/main.py`
- Modify: `backend/.env.example`

- [ ] **Step 1: Create MCP management API**

Create `backend/app/api/mcp.py`:

```python
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
    return get_mcp_manager().list_servers()


@router.post("/servers", status_code=201)
async def register_mcp_server(data: MCPServerCreate) -> dict[str, Any]:
    """Register and connect to an MCP server. Discovered tools are auto-registered."""
    from app.protocols.mcp_client import MCPServerConfig

    mgr = get_mcp_manager()
    config = MCPServerConfig(name=data.name, command=data.command, args=data.args, env=data.env)
    mgr.add_config(config)
    try:
        tools = await mgr.connect(data.name)
        return {"name": data.name, "status": "connected", "tools_registered": tools}
    except Exception as e:
        mgr.remove_config(data.name)
        raise HTTPException(status_code=500, detail=f"Failed to connect: {e}")


@router.get("/servers/{name}")
async def get_mcp_server(name: str) -> dict[str, Any]:
    """Get details of a specific MCP server."""
    mgr = get_mcp_manager()
    config = mgr.get_config(name)
    if config is None:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")
    servers = mgr.list_servers()
    return next((s for s in servers if s["name"] == name), {"name": name, "connected": False})


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
    await mgr.disconnect(name)
    try:
        tools = await mgr.connect(name)
        return {"name": name, "status": "reconnected", "tools_registered": tools}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reconnect failed: {e}")
```

- [ ] **Step 2: Mount MCP router**

Add to `backend/app/api/router.py`:

```python
from app.api.mcp import router as mcp_router

api_router.include_router(mcp_router, prefix="/mcp", tags=["MCP"])
```

- [ ] **Step 3: Add MCP cleanup to lifespan**

In `backend/app/main.py`, add to the shutdown section (before `dispose_redis()`):

```python
    # Disconnect MCP servers
    from app.protocols.mcp_registry import get_mcp_manager
    await get_mcp_manager().disconnect_all()
    logger.info("mcp_servers_disconnected")
```

- [ ] **Step 4: Update .env.example**

Add after the Tool System section:

```env
# Agent Protocol
MCP_SERVER_ENABLED=true
MCP_CLIENT_ENABLED=true
AGENT_MESSAGE_TTL_SECONDS=3600
WEBSOCKET_AGENT_HEARTBEAT_SECONDS=30
```

- [ ] **Step 5: Verify endpoints**

Run: `cd backend && python -c "from app.api.mcp import router; print(f'{len(router.routes)} MCP routes'); print('OK')"`

- [ ] **Step 6: Lint, format, commit**

```bash
cd backend && ruff check app/protocols/ app/api/mcp.py && ruff format app/protocols/ app/api/mcp.py
git add app/api/mcp.py app/api/router.py app/main.py .env.example
git commit -m "feat(protocols): add MCP management API, router mount, and lifecycle hooks

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Full Test Suite + Final Commit & Push

- [ ] **Step 1: Run all tests**

```bash
cd backend && pytest tests/unit/ tests/integration/ -v --tb=short
```
Expected: ALL PASS

- [ ] **Step 2: Run lint + format**

```bash
cd backend && ruff check app/ && ruff format --check app/
```
Expected: CLEAN

- [ ] **Step 3: Verify protocol system**

```bash
cd backend && python -c "
from app.protocols.base import ProtocolType, AgentMessage
from app.protocols.router import ProtocolRouter
from app.protocols.message_bus import AgentMessageBus
from app.protocols.mcp_client import MCPClient, MCPServerConfig
from app.protocols.mcp_server import MassClawMCPServer
from app.protocols.mcp_registry import get_mcp_manager

r = ProtocolRouter()
print(f'Protocol adapters: {list(r._adapters.keys())}')
s = MassClawMCPServer()
print(f'MCP server tools: {s.get_tool_names()}')
m = get_mcp_manager()
print(f'MCP servers: {m.list_servers()}')
print('ALL OK')
"
```

- [ ] **Step 4: Final commit and push**

```bash
git add -A
git status
git commit -m "feat(protocols): MassClaw Agent Protocol Layer — complete implementation

Multi-protocol agent communication:
- MCP Client: connect to external MCP servers, auto-register discovered tools
- MCP Server: expose MassClaw as 5 MCP tools (submit_workflow, get_status, etc.)
- Agent Message Bus: Redis pub/sub direct + broadcast messaging with request-response
- HTTP + WebSocket protocol adapters with unified ProtocolRouter
- MCP management API: register/connect/disconnect/reconnect servers
- Agent model extended with protocol_type field

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"

git push origin main
```

---

## Verification Checklist

1. `GET /api/v1/mcp/servers` — returns empty list (no servers configured yet)
2. `POST /api/v1/mcp/servers` with a filesystem MCP server — tools auto-registered
3. `GET /api/v1/tools` — shows both built-in tools AND MCP tools
4. Agent message bus: send direct message, receive via subscription
5. MCP server exposes 5 tools: submit_workflow, get_workflow_status, get_workflow_result, search_agents, query_memory
6. `ruff check app/` → zero violations
7. `pytest tests/unit/ tests/integration/ -v` → all pass
8. `git log --oneline -10` → clean commit history
