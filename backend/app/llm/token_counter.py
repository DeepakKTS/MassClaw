from __future__ import annotations

from decimal import Decimal

from app.core.logging import get_logger

logger = get_logger(__name__)

# Pricing per 1M tokens (input/output) as of early 2025
# Source: Anthropic and OpenAI published pricing
PRICING_TABLE: dict[str, dict[str, float]] = {
    # Anthropic Claude models
    "claude-opus-4-20250514": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-haiku-3-5-20241022": {"input": 0.80, "output": 4.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    # Aliases
    "claude-opus": {"input": 15.00, "output": 75.00},
    "claude-sonnet": {"input": 3.00, "output": 15.00},
    "claude-haiku": {"input": 0.80, "output": 4.00},
    # Cache hit (zero cost)
    "cache": {"input": 0.0, "output": 0.0},
    # OpenAI models
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    # Local / default fallback
    "local": {"input": 0.0, "output": 0.0},
    "default": {"input": 3.00, "output": 15.00},
}

# Average characters per token (rough approximation)
CHARS_PER_TOKEN_ESTIMATE = 4.0


def estimate_tokens(text: str) -> int:
    """Estimate token count from text length.

    Uses a character-based heuristic: ~4 characters per token on average.
    For production accuracy, use the model-specific tokenizer via count_tokens().
    """
    return max(1, int(len(text) / CHARS_PER_TOKEN_ESTIMATE))


def count_tokens(text: str, model: str) -> int:
    """Count tokens using the appropriate tokenizer for the model.

    Falls back to character-based estimation if the tokenizer isn't available.
    """
    try:
        if model.startswith("gpt") or model.startswith("o1"):
            import tiktoken

            try:
                encoding = tiktoken.encoding_for_model(model)
            except KeyError:
                encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        elif model.startswith("claude"):
            # Anthropic SDK has a token counting utility
            try:
                from anthropic import Anthropic

                client = Anthropic()
                return client.count_tokens(text)
            except Exception:
                return estimate_tokens(text)
        else:
            return estimate_tokens(text)
    except ImportError:
        return estimate_tokens(text)


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> Decimal:
    """Estimate the cost of an LLM call based on token counts and model pricing.

    Returns cost in USD as a Decimal for precision.
    """
    pricing = PRICING_TABLE.get(model, PRICING_TABLE["default"])

    input_cost = Decimal(str(input_tokens)) * Decimal(str(pricing["input"])) / Decimal("1000000")
    output_cost = Decimal(str(output_tokens)) * Decimal(str(pricing["output"])) / Decimal("1000000")

    return input_cost + output_cost


def estimate_call_cost(
    prompt_text: str,
    expected_output_tokens: int = 1000,
    model: str = "claude-sonnet",
) -> Decimal:
    """Estimate total cost for a single LLM call given prompt text and expected output.

    Convenience wrapper combining token estimation and cost calculation.
    """
    input_tokens = estimate_tokens(prompt_text)
    return estimate_cost(input_tokens, expected_output_tokens, model)


def tokens_to_credits(cost_usd: Decimal) -> float:
    """Convert USD cost to MassClaw credits.

    1 credit = $0.001, so $0.01 = 10 credits.
    """
    return float(cost_usd / Decimal("0.001"))


def credits_to_usd(credit_amount: float) -> Decimal:
    """Convert MassClaw credits to USD."""
    return Decimal(str(credit_amount)) * Decimal("0.001")


def get_model_pricing(model: str) -> dict[str, float]:
    """Look up pricing for a model."""
    return PRICING_TABLE.get(model, PRICING_TABLE["default"])


def list_supported_models() -> list[str]:
    """List all models with known pricing."""
    return [k for k in PRICING_TABLE if k != "default"]
