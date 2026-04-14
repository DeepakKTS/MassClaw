from __future__ import annotations

import enum
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ExecutionMode(str, enum.Enum):
    IN_PROCESS = "in_process"
    SANDBOXED = "sandboxed"


@dataclass
class ToolContext:
    workflow_id: uuid.UUID
    agent_id: uuid.UUID
    workspace_path: str
    timeout_seconds: int = 30
    max_iterations: int = 5


@dataclass
class ToolResult:
    content: str
    success: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    cost_credits: float = 0.0


class ToolProvider(ABC):
    """Abstract base class for all MassClaw tools (built-in and third-party)."""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    execution_mode: ExecutionMode
    required_capabilities: set[str]
    estimated_cost_credits: float = 0.0

    @abstractmethod
    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        """Execute the tool with the given arguments."""

    def to_schema(self) -> dict[str, Any]:
        """Return the tool schema in the format expected by LLM providers."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters_schema,
        }
