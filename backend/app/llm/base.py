from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.llm.token_counter import estimate_cost


@dataclass
class ToolCall:
    """Represents a tool call requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMChunk:
    """A single chunk from a streaming LLM response."""

    content: str = ""
    is_final: bool = False
    tool_calls: list[ToolCall] | None = None
    error: str | None = None


@dataclass
class LLMResponse:
    """Complete response from an LLM call with full metrics."""

    content: str
    input_tokens: int
    output_tokens: int
    model: str
    latency_ms: float
    tool_calls: list[ToolCall] | None = None
    cost: Decimal = Decimal("0")
    raw_response: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def compute_cost(self) -> Decimal:
        """Compute cost from token usage and model pricing."""
        self.cost = estimate_cost(self.input_tokens, self.output_tokens, self.model)
        return self.cost


class LLMProvider(ABC):
    """Abstract interface for LLM providers.

    All MassClaw agent calls go through this interface, enabling:
    - Provider swapping (Anthropic, OpenAI, local)
    - Unified metrics collection
    - Circuit breaker integration
    - Cost tracking
    """

    provider_name: str = "base"

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        tools: list[dict] | None = None,
        stop_sequences: list[str] | None = None,
    ) -> LLMResponse:
        """Generate a complete response from the LLM."""
        ...

    @abstractmethod
    async def stream(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[LLMChunk]:
        """Stream a response from the LLM, yielding chunks."""
        ...

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Count tokens for a given text using this provider's tokenizer."""
        ...

    @abstractmethod
    def default_model(self) -> str:
        """Return the default model for this provider."""
        ...

    def _start_timer(self) -> float:
        return time.perf_counter()

    def _elapsed_ms(self, start: float) -> float:
        return (time.perf_counter() - start) * 1000
