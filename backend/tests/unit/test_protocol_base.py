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
