from __future__ import annotations

import asyncio
import threading
from typing import Any

from app.config import get_settings
from app.core.logging import get_logger
from app.embeddings.models import MODEL_REGISTRY, EmbeddingModelInfo, get_model_info

logger = get_logger(__name__)

# Module-level singleton
_service: EmbeddingService | None = None


class EmbeddingService:
    """Thread-safe embedding generation with model hot-swap capability.

    The sentence-transformers model is loaded once and kept in memory.
    All embedding computation is offloaded to a thread pool via asyncio.to_thread()
    to avoid blocking the async event loop.
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._model_name: str = ""
        self._model_info: EmbeddingModelInfo | None = None
        self._load_error: str | None = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._model_info.dimensions if self._model_info else 384

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load_model(self, model_name: str | None = None) -> None:
        """Load a sentence-transformers model synchronously.

        Thread-safe: uses a lock to prevent concurrent loads during hot-swap.
        Raises RuntimeError if the model cannot be loaded.
        """
        if model_name is None:
            model_name = get_settings().embedding_model

        with self._lock:
            if self._model is not None and self._model_name == model_name:
                logger.debug("embedding_model_already_loaded", model=model_name)
                return

            info = get_model_info(model_name)

            logger.info("loading_embedding_model", model=model_name, dimensions=info.dimensions)
            try:
                from sentence_transformers import SentenceTransformer

                new_model = SentenceTransformer(model_name)
            except Exception as e:
                self._load_error = str(e)
                logger.error(
                    "embedding_model_load_failed",
                    model=model_name,
                    error=str(e),
                )
                raise RuntimeError(f"Failed to load embedding model '{model_name}': {e}") from e

            # Atomic swap
            old_model = self._model
            self._model = new_model
            self._model_name = model_name
            self._model_info = info
            self._load_error = None

            # Free old model
            del old_model

            logger.info("embedding_model_loaded", model=model_name, dimensions=info.dimensions)

    def _ensure_loaded(self) -> None:
        if self._model is None:
            if self._load_error:
                raise RuntimeError(
                    f"Embedding model unavailable (previous load failed: {self._load_error}). "
                    "Restart the service or call load_model() to retry."
                )
            self.load_model()

    def _embed_sync(self, text: str) -> list[float]:
        """Generate embedding synchronously (called in thread pool)."""
        self._ensure_loaded()
        with self._lock:
            embedding = self._model.encode(text, normalize_embeddings=True)
        return embedding.tolist()

    def _embed_batch_sync(self, texts: list[str]) -> list[list[float]]:
        """Generate batch embeddings synchronously (called in thread pool)."""
        self._ensure_loaded()
        with self._lock:
            embeddings = self._model.encode(texts, normalize_embeddings=True, batch_size=32)
        return [e.tolist() for e in embeddings]

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text. Non-blocking.

        Offloads computation to a thread pool so the event loop stays free.
        """
        return await asyncio.to_thread(self._embed_sync, text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts. Non-blocking.

        Uses sentence-transformers batch encoding for efficiency.
        """
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_batch_sync, texts)

    async def hot_swap_model(self, model_name: str) -> EmbeddingModelInfo:
        """Atomically swap the embedding model. Non-blocking.

        The old model is replaced in-place; in-flight embed calls
        that already acquired the lock will finish with the old model.
        New calls will use the new model.
        """
        info = get_model_info(model_name)  # Validate before loading
        await asyncio.to_thread(self.load_model, model_name)
        return info

    def get_status(self) -> dict:
        """Return current service status for monitoring."""
        return {
            "loaded": self.is_loaded,
            "model_name": self._model_name,
            "dimensions": self.dimensions,
            "available_models": list(MODEL_REGISTRY.keys()),
        }


def get_embedding_service() -> EmbeddingService:
    """Get the module-level embedding service singleton."""
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service


async def init_embedding_service() -> EmbeddingService:
    """Initialize the embedding service and preload the configured model.

    If the model fails to load (e.g., network timeout, missing model), the
    service starts in a degraded state. Embedding-dependent features (semantic
    search, injection detection) will be unavailable, but the app still serves
    non-embedding endpoints.
    """
    service = get_embedding_service()
    if not service.is_loaded:
        try:
            await asyncio.to_thread(service.load_model)
        except RuntimeError as e:
            logger.error(
                "embedding_service_degraded",
                error=str(e),
                hint="App will start but embedding features are unavailable.",
            )
    return service
