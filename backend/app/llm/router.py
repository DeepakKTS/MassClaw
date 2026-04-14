from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.core.logging import get_logger
from app.llm.base import LLMProvider, LLMResponse

logger = get_logger(__name__)

# Module-level singleton
_router: ModelRouter | None = None


class ModelRouter:
    """Routes LLM requests to the appropriate provider.

    Supports:
    - Explicit model selection (model name determines provider)
    - Fallback when a provider is unavailable
    - Provider health tracking
    """

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._initialize_providers()

    def _initialize_providers(self) -> None:
        """Initialize available providers based on configured API keys."""
        settings = get_settings()

        if settings.anthropic_api_key:
            try:
                from app.llm.anthropic import AnthropicProvider

                self._providers["anthropic"] = AnthropicProvider()
                logger.info("llm_provider_initialized", provider="anthropic")
            except Exception as e:
                logger.warning("llm_provider_failed", provider="anthropic", error=str(e))

        if settings.openai_api_key:
            try:
                from app.llm.openai import OpenAIProvider

                self._providers["openai"] = OpenAIProvider()
                logger.info("llm_provider_initialized", provider="openai")
            except Exception as e:
                logger.warning("llm_provider_failed", provider="openai", error=str(e))

        if not self._providers:
            if settings.auth_required:
                raise RuntimeError(
                    "FATAL: No LLM providers available. "
                    "Configure ANTHROPIC_API_KEY or OPENAI_API_KEY when AUTH_REQUIRED=true. "
                    "Mock provider is only allowed in development mode (AUTH_REQUIRED=false)."
                )
            from app.llm.mock import MockProvider

            self._providers["mock"] = MockProvider()
            logger.warning(
                "llm_mock_provider_activated",
                reason="no_api_keys_configured",
                warning="Mock provider returns fake data. Not suitable for production.",
            )

    def _resolve_provider(self, model: str | None = None) -> tuple[LLMProvider, str]:
        """Resolve which provider and model to use.

        Returns (provider, model_name) tuple.
        """
        if model:
            # Route based on model name prefix
            if model.startswith("claude"):
                provider = self._providers.get("anthropic")
                if provider:
                    return provider, model
            elif model.startswith("gpt") or model.startswith("o1"):
                provider = self._providers.get("openai")
                if provider:
                    return provider, model

        # Default: prefer Anthropic, fall back to OpenAI, then mock
        for name in ("anthropic", "openai", "mock"):
            provider = self._providers.get(name)
            if provider:
                return provider, model or provider.default_model()

        raise RuntimeError("No LLM providers available. Configure ANTHROPIC_API_KEY or OPENAI_API_KEY.")

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
        """Route a generation request to the appropriate provider."""
        provider, resolved_model = self._resolve_provider(model)

        return await provider.generate(
            prompt=prompt,
            system=system,
            model=resolved_model,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            stop_sequences=stop_sequences,
        )

    @property
    def available_providers(self) -> list[str]:
        return list(self._providers.keys())

    def get_status(self) -> dict[str, Any]:
        return {
            "providers": self.available_providers,
            "default_provider": self.available_providers[0] if self.available_providers else None,
        }


def get_model_router() -> ModelRouter:
    """Get the module-level model router singleton."""
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router
