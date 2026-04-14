from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anthropic
import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.core.logging import get_logger
from app.llm.base import LLMChunk, LLMProvider, LLMResponse, ToolCall
from app.llm.token_counter import estimate_tokens

logger = get_logger(__name__)


class AnthropicProvider(LLMProvider):
    """Anthropic Claude LLM provider using the official SDK."""

    provider_name = "anthropic"

    DEFAULT_MODEL = "claude-sonnet-4-20250514"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for Anthropic provider")
        self.client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10.0),
        )
        self._max_retries = settings.llm_max_retries

    def default_model(self) -> str:
        return self.DEFAULT_MODEL

    def count_tokens(self, text: str) -> int:
        return estimate_tokens(text)

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
        """Generate a complete response from Claude."""
        model = model or self.DEFAULT_MODEL
        start = self._start_timer()

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }

        if system:
            kwargs["system"] = system
        if stop_sequences:
            kwargs["stop_sequences"] = stop_sequences
        if tools:
            kwargs["tools"] = self._format_tools(tools)

        response = await self._call_with_retry(**kwargs)

        latency_ms = self._elapsed_ms(start)

        # Extract content and tool calls
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                    )
                )

        content = "\n".join(content_parts)

        result = LLMResponse(
            content=content,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=model,
            latency_ms=round(latency_ms, 2),
            tool_calls=tool_calls if tool_calls else None,
            raw_response=response,
            metadata={
                "stop_reason": response.stop_reason,
                "provider": self.provider_name,
            },
        )
        result.compute_cost()

        logger.info(
            "llm_call_completed",
            provider="anthropic",
            model=model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            cost_usd=str(result.cost),
            stop_reason=response.stop_reason,
        )

        return result

    async def _call_with_retry(self, **kwargs: Any) -> Any:
        """Call the Anthropic API with retry logic."""

        @retry(
            retry=retry_if_exception_type(
                (
                    anthropic.RateLimitError,
                    anthropic.APITimeoutError,
                    anthropic.APIConnectionError,
                )
            ),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            before_sleep=lambda retry_state: logger.warning(
                "anthropic_api_retry",
                attempt=retry_state.attempt_number,
                wait_seconds=round(retry_state.next_action.sleep, 1)  # type: ignore[union-attr]
                if retry_state.next_action
                else 0,
                error=str(retry_state.outcome.exception()) if retry_state.outcome else "unknown",
            ),
            reraise=True,
        )
        async def _do_call() -> Any:
            return await self.client.messages.create(**kwargs)

        return await _do_call()

    async def stream(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[LLMChunk]:
        """Stream a response from Claude, yielding chunks."""
        model = model or self.DEFAULT_MODEL

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        try:
            async with self.client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield LLMChunk(content=text)

                # Final chunk with complete metadata
                await stream.get_final_message()
                yield LLMChunk(
                    content="",
                    is_final=True,
                )
        except (
            anthropic.APIError,
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
        ) as exc:
            logger.error(
                "anthropic_stream_error",
                error=str(exc),
                error_type=type(exc).__name__,
                model=model,
            )
            yield LLMChunk(
                content="",
                is_final=True,
                error=f"Anthropic stream failed: {type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _format_tools(tools: list[dict]) -> list[dict]:
        """Format tools into Anthropic's tool format."""
        formatted = []
        for tool in tools:
            formatted.append(
                {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", tool.get("input_schema", {})),
                }
            )
        return formatted
