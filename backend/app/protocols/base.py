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
        """Send a message to an agent via this protocol."""

    @abstractmethod
    async def health_check(self, agent: Agent) -> bool:
        """Check if an agent is reachable via this protocol."""
