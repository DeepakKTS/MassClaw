from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.config import get_settings
from app.core.logging import get_logger
from app.llm.base import LLMChunk, LLMProvider, LLMResponse, ToolCall
from app.llm.token_counter import estimate_tokens

logger = get_logger(__name__)


class OpenAIProvider(LLMProvider):
    """OpenAI GPT LLM provider using the official SDK."""

    provider_name = "openai"

    DEFAULT_MODEL = "gpt-4o"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI provider")

        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    def default_model(self) -> str:
        return self.DEFAULT_MODEL

    def count_tokens(self, text: str) -> int:
        try:
            import tiktoken

            encoding = tiktoken.encoding_for_model(self.DEFAULT_MODEL)
            return len(encoding.encode(text))
        except Exception:
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
        """Generate a complete response from OpenAI."""
        model = model or self.DEFAULT_MODEL
        start = self._start_timer()

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if stop_sequences:
            kwargs["stop"] = stop_sequences
        if tools:
            kwargs["tools"] = self._format_tools(tools)

        response = await self.client.chat.completions.create(**kwargs)

        latency_ms = self._elapsed_ms(start)
        choice = response.choices[0]

        # Extract tool calls
        tool_calls: list[ToolCall] = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments),
                    )
                )

        result = LLMResponse(
            content=choice.message.content or "",
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            model=model,
            latency_ms=round(latency_ms, 2),
            tool_calls=tool_calls if tool_calls else None,
            raw_response=response,
            metadata={
                "finish_reason": choice.finish_reason,
                "provider": self.provider_name,
            },
        )
        result.compute_cost()

        logger.info(
            "llm_call_completed",
            provider="openai",
            model=model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            cost_usd=str(result.cost),
        )

        return result

    async def stream(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[LLMChunk]:
        """Stream a response from OpenAI."""
        model = model or self.DEFAULT_MODEL

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        stream = await self.client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
            stream=True,
        )

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield LLMChunk(content=chunk.choices[0].delta.content)

        yield LLMChunk(content="", is_final=True)

    @staticmethod
    def _format_tools(tools: list[dict]) -> list[dict]:
        """Format tools into OpenAI's function calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", tool.get("input_schema", {})),
                },
            }
            for tool in tools
        ]
