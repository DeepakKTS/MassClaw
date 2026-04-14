from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

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


class OpenAIProvider(LLMProvider):
    """OpenAI GPT LLM provider using the official SDK."""

    provider_name = "openai"

    DEFAULT_MODEL = "gpt-4o"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI provider")

        import openai

        self._openai = openai
        self.client = openai.AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10.0),
        )
        self._max_retries = settings.llm_max_retries

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

        response = await self._call_with_retry(**kwargs)

        latency_ms = self._elapsed_ms(start)
        choice = response.choices[0]

        # Extract tool calls
        tool_calls: list[ToolCall] = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    logger.warning(
                        "openai_tool_call_json_parse_error",
                        tool_name=tc.function.name,
                        raw_arguments=tc.function.arguments,
                    )
                    arguments = {}
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=arguments,
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

    async def _call_with_retry(self, **kwargs: Any) -> Any:
        """Call the OpenAI API with retry logic."""

        @retry(
            retry=retry_if_exception_type(
                (
                    self._openai.RateLimitError,
                    self._openai.APITimeoutError,
                    self._openai.APIConnectionError,
                )
            ),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            before_sleep=lambda retry_state: logger.warning(
                "openai_api_retry",
                attempt=retry_state.attempt_number,
                wait_seconds=round(retry_state.next_action.sleep, 1)  # type: ignore[union-attr]
                if retry_state.next_action
                else 0,
                error=str(retry_state.outcome.exception()) if retry_state.outcome else "unknown",
            ),
            reraise=True,
        )
        async def _do_call() -> Any:
            return await self.client.chat.completions.create(**kwargs)

        return await _do_call()

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

        try:
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
        except (
            self._openai.APIError,
            self._openai.APIConnectionError,
            self._openai.APITimeoutError,
        ) as exc:
            logger.error(
                "openai_stream_error",
                error=str(exc),
                error_type=type(exc).__name__,
                model=model,
            )
            yield LLMChunk(
                content="",
                is_final=True,
                error=f"OpenAI stream failed: {type(exc).__name__}: {exc}",
            )

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
