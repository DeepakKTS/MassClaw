from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingModelInfo:
    """Metadata about a supported embedding model."""

    name: str
    dimensions: int
    max_tokens: int
    description: str


# Registry of supported embedding models
MODEL_REGISTRY: dict[str, EmbeddingModelInfo] = {
    "all-MiniLM-L6-v2": EmbeddingModelInfo(
        name="all-MiniLM-L6-v2",
        dimensions=384,
        max_tokens=256,
        description="Fast, lightweight 384-dim model. Good for general-purpose semantic similarity.",
    ),
    "all-MiniLM-L12-v2": EmbeddingModelInfo(
        name="all-MiniLM-L12-v2",
        dimensions=384,
        max_tokens=256,
        description="Higher quality 384-dim model. Slower but more accurate than L6.",
    ),
    "all-mpnet-base-v2": EmbeddingModelInfo(
        name="all-mpnet-base-v2",
        dimensions=768,
        max_tokens=384,
        description="High quality 768-dim model. Best accuracy for general tasks.",
    ),
    "paraphrase-MiniLM-L6-v2": EmbeddingModelInfo(
        name="paraphrase-MiniLM-L6-v2",
        dimensions=384,
        max_tokens=128,
        description="Optimized for paraphrase detection and semantic textual similarity.",
    ),
}


def get_model_info(model_name: str) -> EmbeddingModelInfo:
    """Look up model info from the registry."""
    if model_name not in MODEL_REGISTRY:
        raise ValueError(
            f"Unknown embedding model '{model_name}'. "
            f"Available: {', '.join(MODEL_REGISTRY.keys())}"
        )
    return MODEL_REGISTRY[model_name]


def list_models() -> list[EmbeddingModelInfo]:
    """List all available embedding models."""
    return list(MODEL_REGISTRY.values())
