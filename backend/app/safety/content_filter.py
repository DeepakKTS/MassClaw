"""Layered content filtering system for MassClaw's safety layer.

The filter executes three layers in sequence:

1. **Regex layer** -- compiled patterns for PII detection (SSN, credit
   cards, emails, phone numbers, IP addresses) and known harmful content
   patterns.
2. **NLP classifier layer** -- sentence-transformers embeddings compared
   via cosine similarity against a bank of known harmful content
   embeddings.  Categories: ``harmful``, ``pii``, ``sensitive``, ``safe``.
3. **LLM verification layer** (optional) -- triggered only when layers 1-2
   flag content with *medium* confidence.  Sends flagged content to an LLM
   with a safety evaluation prompt for a final ruling.

All regex patterns are pre-compiled at module level for performance.
NLP embeddings are computed via ``asyncio.to_thread()`` using the
application's shared ``EmbeddingService``.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.embeddings.service import get_embedding_service

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Compiled PII regex patterns (module-level for performance)
# ---------------------------------------------------------------------------

_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CREDIT_CARD_RE = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
_PHONE_RE = re.compile(
    r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
)
_IP_ADDRESS_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)

_PII_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("ssn", _SSN_RE, "[REDACTED_SSN]"),
    ("credit_card", _CREDIT_CARD_RE, "[REDACTED_CC]"),
    ("email", _EMAIL_RE, "[REDACTED_EMAIL]"),
    ("phone", _PHONE_RE, "[REDACTED_PHONE]"),
    ("ip_address", _IP_ADDRESS_RE, "[REDACTED_IP]"),
]

# ---------------------------------------------------------------------------
# Known harmful content patterns (regex layer)
# ---------------------------------------------------------------------------

_HARMFUL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("sql_injection", re.compile(
        r"(?i)(?:(?:union\s+select)|(?:drop\s+table)|(?:insert\s+into)|"
        r"(?:delete\s+from)|(?:update\s+\w+\s+set)|(?:;\s*--)|(?:'\s*or\s+'1'\s*=\s*'1))"
    )),
    ("xss_attempt", re.compile(
        r"(?i)<\s*script[^>]*>|javascript\s*:|on(?:error|load|click)\s*="
    )),
    ("path_traversal", re.compile(
        r"(?:\.\./){2,}|(?:\.\.\%2[fF]){2,}"
    )),
    ("command_injection", re.compile(
        r"(?:;\s*(?:rm|cat|wget|curl|bash|sh|python|perl|nc)\s)|(?:\|\s*(?:bash|sh)\b)"
    )),
]

# ---------------------------------------------------------------------------
# NLP category reference embeddings (seed phrases)
# ---------------------------------------------------------------------------

_CATEGORY_SEEDS: dict[str, list[str]] = {
    "harmful": [
        "how to create a weapon",
        "instructions for making explosives",
        "detailed guide to harm someone",
        "bypass security and hack into systems",
        "generate malware or virus code",
    ],
    "pii": [
        "my social security number is",
        "here is my credit card number",
        "personal identification information",
        "home address and date of birth",
        "passport number and bank account details",
    ],
    "sensitive": [
        "confidential company financial data",
        "internal trade secrets",
        "private medical records",
        "classified government information",
        "proprietary algorithm details",
    ],
    "safe": [
        "hello how are you today",
        "please summarise this document",
        "what is the weather forecast",
        "explain quantum computing simply",
        "write a poem about nature",
    ],
}

# Default similarity thresholds per category.
_DEFAULT_THRESHOLDS: dict[str, float] = {
    "harmful": 0.65,
    "pii": 0.60,
    "sensitive": 0.55,
    "safe": 0.70,
}

# Confidence band for triggering the LLM verification layer.
_MEDIUM_CONFIDENCE_LOW = 0.40
_MEDIUM_CONFIDENCE_HIGH = 0.75


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PIIMatch:
    """A single PII match detected by the regex layer."""

    type: str
    """PII category: ``ssn``, ``credit_card``, ``email``, ``phone``, ``ip_address``."""

    pattern: str
    """The regex pattern that matched (human-readable label)."""

    location: tuple[int, int]
    """``(start, end)`` character offsets in the original content."""

    redacted: str
    """The replacement placeholder, e.g. ``[REDACTED_SSN]``."""


@dataclass(slots=True)
class ContentAnalysis:
    """Aggregated result of all content filter layers."""

    is_safe: bool
    """Overall safety verdict."""

    categories: dict[str, float] = field(default_factory=dict)
    """Similarity score (0-1) for each NLP category."""

    pii_detected: list[PIIMatch] = field(default_factory=list)
    """All PII matches found in the content."""

    flags: list[str] = field(default_factory=list)
    """Human-readable flags raised during analysis."""

    confidence: float = 1.0
    """Confidence in the verdict (0-1).  Lower when the LLM layer was not
    invoked despite medium-confidence NLP results."""


# ---------------------------------------------------------------------------
# Content Filter
# ---------------------------------------------------------------------------

class ContentFilter:
    """Layered content filtering engine.

    Instantiate once (or per-request -- it is lightweight) and call
    :meth:`analyze` to run the full pipeline.

    Parameters
    ----------
    thresholds:
        Optional per-category similarity thresholds overriding the defaults.
    enable_llm_verification:
        Whether to enable the optional LLM verification layer for
        medium-confidence results.  Defaults to ``True``.
    """

    def __init__(
        self,
        *,
        thresholds: dict[str, float] | None = None,
        enable_llm_verification: bool = True,
    ) -> None:
        self._thresholds = {**_DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._enable_llm_verification = enable_llm_verification

        # Lazily computed category reference embeddings.
        self._category_embeddings: dict[str, list[list[float]]] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(self, content: str) -> ContentAnalysis:
        """Run the full content analysis pipeline.

        Executes the regex layer, then the NLP layer.  If the NLP layer
        produces medium-confidence results and LLM verification is enabled,
        the LLM layer is invoked for a final ruling.

        Parameters
        ----------
        content:
            The text to analyse.

        Returns
        -------
        ContentAnalysis
        """
        if not content or not content.strip():
            return ContentAnalysis(is_safe=True, confidence=1.0)

        # Layer 1: Regex
        pii_matches = self._detect_pii(content)
        harmful_flags = self._detect_harmful_patterns(content)

        # Layer 2: NLP classifier
        category_scores = await self._classify_content(content)

        # Combine signals -----------------------------------------------
        flags: list[str] = list(harmful_flags)

        if pii_matches:
            flags.append(f"pii_detected:{len(pii_matches)}")

        is_safe = True
        for category, score in category_scores.items():
            if category == "safe":
                continue
            threshold = self._thresholds.get(category, 0.6)
            if score >= threshold:
                is_safe = False
                flags.append(f"nlp:{category}:{score:.2f}")

        if harmful_flags:
            is_safe = False

        if pii_matches:
            is_safe = False

        # Compute raw confidence from NLP scores.
        unsafe_scores = [
            v for k, v in category_scores.items() if k != "safe"
        ]
        max_unsafe = max(unsafe_scores) if unsafe_scores else 0.0
        safe_score = category_scores.get("safe", 0.0)

        # Confidence is high when the signal is clearly safe OR clearly
        # unsafe.  It is low in the ambiguous middle band.
        if is_safe:
            confidence = max(0.5, safe_score)
        else:
            confidence = max(0.5, max_unsafe)

        # Layer 3: Optional LLM verification for medium-confidence flags
        if (
            self._enable_llm_verification
            and flags
            and _MEDIUM_CONFIDENCE_LOW <= confidence <= _MEDIUM_CONFIDENCE_HIGH
        ):
            llm_result = await self._llm_verify(content, flags)
            if llm_result is not None:
                is_safe = llm_result["is_safe"]
                confidence = llm_result.get("confidence", confidence)
                if llm_result.get("flag"):
                    flags.append(f"llm:{llm_result['flag']}")

        return ContentAnalysis(
            is_safe=is_safe,
            categories=category_scores,
            pii_detected=pii_matches,
            flags=flags,
            confidence=round(confidence, 4),
        )

    async def redact_pii(self, content: str) -> str:
        """Replace all detected PII in *content* with placeholder tokens.

        Parameters
        ----------
        content:
            The original text.

        Returns
        -------
        str
            The text with PII replaced by ``[REDACTED_TYPE]`` tokens.
        """
        matches = self._detect_pii(content)
        if not matches:
            return content

        # Sort matches by start position descending so replacements don't
        # shift subsequent offsets.
        sorted_matches = sorted(matches, key=lambda m: m.location[0], reverse=True)
        result = content
        for match in sorted_matches:
            start, end = match.location
            result = result[:start] + match.redacted + result[end:]

        return result

    # ------------------------------------------------------------------
    # Layer 1: Regex
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_pii(content: str) -> list[PIIMatch]:
        """Scan *content* for PII using pre-compiled regex patterns.

        Returns a list of ``PIIMatch`` instances for every match found.
        """
        matches: list[PIIMatch] = []
        for pii_type, pattern, redacted in _PII_PATTERNS:
            for m in pattern.finditer(content):
                matches.append(
                    PIIMatch(
                        type=pii_type,
                        pattern=pattern.pattern,
                        location=(m.start(), m.end()),
                        redacted=redacted,
                    )
                )
        return matches

    @staticmethod
    def _detect_harmful_patterns(content: str) -> list[str]:
        """Scan *content* for known harmful patterns.

        Returns a list of flag strings (one per distinct pattern category
        that matched).
        """
        flags: list[str] = []
        for label, pattern in _HARMFUL_PATTERNS:
            if pattern.search(content):
                flags.append(f"regex:{label}")
        return flags

    # ------------------------------------------------------------------
    # Layer 2: NLP classifier
    # ------------------------------------------------------------------

    async def _classify_content(self, content: str) -> dict[str, float]:
        """Compute similarity scores between *content* and category seeds.

        Uses the application's ``EmbeddingService`` to generate embeddings.
        All heavy computation is offloaded via ``asyncio.to_thread()``.

        Returns a dict mapping category name to the maximum cosine similarity
        against that category's seed embeddings.
        """
        embedding_service = get_embedding_service()

        if not embedding_service.is_loaded:
            logger.warning("embedding_service_not_loaded_skipping_nlp_layer")
            return {cat: 0.0 for cat in _CATEGORY_SEEDS}

        # Ensure category reference embeddings are computed.
        if self._category_embeddings is None:
            await self._compute_category_embeddings()

        # Embed the input content.
        try:
            content_embedding = await embedding_service.embed(content)
        except Exception:
            logger.warning("content_embedding_failed", exc_info=True)
            return {cat: 0.0 for cat in _CATEGORY_SEEDS}

        # Compute max cosine similarity per category in a thread to avoid
        # blocking the event loop with numerical computation.
        scores = await asyncio.to_thread(
            self._compute_similarities,
            content_embedding,
            self._category_embeddings or {},
        )

        return scores

    async def _compute_category_embeddings(self) -> None:
        """Pre-compute embeddings for all category seed phrases."""
        embedding_service = get_embedding_service()
        self._category_embeddings = {}

        for category, seeds in _CATEGORY_SEEDS.items():
            try:
                embeddings = await embedding_service.embed_batch(seeds)
                self._category_embeddings[category] = embeddings
            except Exception:
                logger.warning(
                    "category_embedding_failed",
                    category=category,
                    exc_info=True,
                )
                self._category_embeddings[category] = []

    @staticmethod
    def _compute_similarities(
        content_embedding: list[float],
        category_embeddings: dict[str, list[list[float]]],
    ) -> dict[str, float]:
        """Compute cosine similarity between the content embedding and every
        category's seed embeddings.

        Returns the maximum similarity per category.  This runs in a worker
        thread to keep the event loop free.
        """
        scores: dict[str, float] = {}
        for category, embeddings in category_embeddings.items():
            if not embeddings:
                scores[category] = 0.0
                continue
            max_sim = 0.0
            for seed_emb in embeddings:
                sim = _cosine_similarity(content_embedding, seed_emb)
                if sim > max_sim:
                    max_sim = sim
            scores[category] = round(max_sim, 4)
        return scores

    # ------------------------------------------------------------------
    # Layer 3: LLM verification
    # ------------------------------------------------------------------

    @staticmethod
    async def _llm_verify(content: str, flags: list[str]) -> dict[str, Any] | None:
        """Send flagged content to an LLM for a definitive safety ruling.

        Only invoked when layers 1-2 produce medium-confidence results.
        Returns ``None`` if the LLM is unavailable or the call fails.
        """
        try:
            from app.llm.router import get_model_router

            router = get_model_router()
        except Exception:
            logger.warning("llm_router_unavailable_for_content_verification")
            return None

        system_prompt = (
            "You are a content safety evaluator for an AI orchestration platform. "
            "Analyse the following content and the flags raised by automated "
            "layers.  Respond with a JSON object containing:\n"
            '- "is_safe": boolean\n'
            '- "confidence": float between 0 and 1\n'
            '- "flag": optional short string label if unsafe (e.g. "harmful", "pii_leak")\n'
            '- "reason": one-sentence explanation\n\n'
            "Respond ONLY with the JSON object, no markdown fencing or extra text."
        )

        user_prompt = (
            f"Content to evaluate:\n---\n{content[:2000]}\n---\n\n"
            f"Automated flags: {', '.join(flags)}\n\n"
            "Provide your safety assessment as JSON."
        )

        try:
            response = await router.generate(
                prompt=user_prompt,
                system=system_prompt,
                max_tokens=256,
                temperature=0.0,
            )
            result = json.loads(response.content.strip())
            if isinstance(result, dict) and "is_safe" in result:
                return result
            logger.warning("llm_verification_unexpected_format", raw=response.content[:200])
            return None
        except json.JSONDecodeError:
            logger.warning("llm_verification_json_parse_failed", exc_info=True)
            return None
        except Exception:
            logger.warning("llm_verification_failed", exc_info=True)
            return None


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Falls back to 0.0 for zero-magnitude vectors.
    """
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)
