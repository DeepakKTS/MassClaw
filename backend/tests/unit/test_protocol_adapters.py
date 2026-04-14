"""Unit tests for HTTP adapter, WebSocket adapter, and ProtocolRouter."""

from __future__ import annotations

import dataclasses
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.protocols.base import AgentMessage, ProtocolType
from app.protocols.http_adapter import HTTPAdapter
from app.protocols.router import ProtocolRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(
    protocol_type: str | None = None,
    endpoint: str = "http://agent-host/api",
    health_check_url: str | None = "http://agent-host/health",
) -> MagicMock:
    agent = MagicMock()
    agent.agent_id = uuid.uuid4()
    agent.endpoint = endpoint
    agent.health_check_url = health_check_url
    agent.protocol_type = protocol_type
    return agent


def _make_message() -> AgentMessage:
    return AgentMessage(
        sender_id="sender-agent",
        recipient_id="target-agent",
        channel="massclaw:agent:direct:target-agent",
        content="test payload",
        message_type="request",
    )


# ---------------------------------------------------------------------------
# HTTPAdapter tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_http_send_message_success():
    """HTTPAdapter.send_message POSTs the message and parses the response."""
    adapter = HTTPAdapter()
    agent = _make_agent()
    message = _make_message()

    response_data = dataclasses.asdict(
        AgentMessage(
            sender_id="target-agent",
            recipient_id="sender-agent",
            channel="massclaw:agent:direct:sender-agent",
            content="response body",
            message_type="response",
            correlation_id=message.message_id,
        )
    )

    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=response_data)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("app.protocols.http_adapter.httpx.AsyncClient") as MockCls:
        MockCls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockCls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await adapter.send_message(agent, message)

    assert result is not None
    assert result.content == "response body"
    assert result.message_type == "response"
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_http_health_check_success():
    """HTTPAdapter.health_check returns True for a 2xx response."""
    adapter = HTTPAdapter()
    agent = _make_agent()

    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.protocols.http_adapter.httpx.AsyncClient") as MockCls:
        MockCls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockCls.return_value.__aexit__ = AsyncMock(return_value=False)

        healthy = await adapter.health_check(agent)

    assert healthy is True


@pytest.mark.asyncio
async def test_http_health_check_no_url():
    """HTTPAdapter.health_check returns False when health_check_url is not set."""
    adapter = HTTPAdapter()
    agent = _make_agent(health_check_url=None)

    healthy = await adapter.health_check(agent)

    assert healthy is False


# ---------------------------------------------------------------------------
# ProtocolRouter tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_router_routes_to_http():
    """ProtocolRouter.send_message delegates to the HTTP adapter for HTTP agents."""
    router = ProtocolRouter()
    agent = _make_agent(protocol_type="http")
    message = _make_message()

    expected_reply = AgentMessage(
        sender_id="target-agent",
        recipient_id="sender-agent",
        channel="massclaw:agent:direct:sender-agent",
        content="reply",
        message_type="response",
    )

    with patch.object(router._http, "send_message", new=AsyncMock(return_value=expected_reply)) as mock_send:
        result = await router.send_message(agent, message)

    mock_send.assert_called_once_with(agent, message)
    assert result is expected_reply


def test_router_get_adapter():
    """ProtocolRouter exposes adapters for both HTTP and WebSocket protocol types."""
    router = ProtocolRouter()

    http_adapter = router.get_adapter(ProtocolType.HTTP)
    ws_adapter = router.get_adapter(ProtocolType.WEBSOCKET)

    assert http_adapter is not None
    assert ws_adapter is not None

    # Verify they are distinct adapter instances
    assert type(http_adapter).__name__ == "HTTPAdapter"
    assert type(ws_adapter).__name__ == "WebSocketAdapter"
