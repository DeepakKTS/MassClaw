"""Client for the MIT NANDA Index ("the phonebook for agents").

The Index maps agent DIDs to discovery metadata so any party on the Internet
of Agents can look up an agent by handle, fetch its signed AgentFacts
document, and know it is authentic. This client is deliberately tolerant of
two response styles that have appeared in NANDA prototypes:

1. **Embedded**: the Index returns the full AgentFacts JSON directly.
2. **Pointer**: the Index returns a small record that includes a
   ``facts_url`` pointing at the agent's ``.well-known/agent-facts.json``.

Either form is accepted. Whichever we end up with, the signature is always
verified against the issuer public key embedded in the DID before the
document is handed back to callers — no trust is placed in the Index or the
network transport.

The client is async, uses ``httpx.AsyncClient``, retries transient failures,
and never holds global state, so it can be instantiated per request or per
long-running worker as needed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.logging import get_logger
from app.identity.agent_facts import AgentFacts
from app.identity.did import parse_did

logger = get_logger(__name__)

_DEFAULT_RETRY_ATTEMPTS = 3
_DEFAULT_USER_AGENT = "MassClaw-NANDA-Index-Client/1.0"


def _default_timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


class NandaIndexError(Exception):
    """Base class for NANDA Index client errors."""


class NandaIndexUnavailable(NandaIndexError):  # noqa: N818 — reads better than 'UnavailableError'
    """Raised when the Index is unreachable or returning 5xx after retries."""


class NandaIndexNotFound(NandaIndexError):  # noqa: N818 — mirrors FileNotFoundError spelling
    """Raised when the Index returns 404 for a DID."""


class NandaIndexInvalidResponse(NandaIndexError):  # noqa: N818 — describes the response shape
    """Raised when the Index returns a response we cannot parse/verify."""


@dataclass(frozen=True)
class NandaIndexConfig:
    """Runtime configuration for the NANDA Index client."""

    base_url: str
    timeout: httpx.Timeout = field(default_factory=_default_timeout)
    retry_attempts: int = _DEFAULT_RETRY_ATTEMPTS
    user_agent: str = _DEFAULT_USER_AGENT
    # Optional bearer token used by NANDA prototypes that require issuer
    # authentication for ``POST`` registration.
    api_token: str | None = None
    # Additional headers (defaults applied on top of these).
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RegistrationResult:
    did: str
    facts_url: str | None
    raw_response: dict[str, Any]


class NandaIndexClient:
    """Minimal but production-grade HTTP client for the NANDA Index."""

    def __init__(
        self,
        config: NandaIndexConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport  # Tests can inject httpx.MockTransport here.

    # ---------------------------------------------------------------- Resolve

    async def resolve(self, did: str) -> AgentFacts | None:
        """Resolve a DID to a verified AgentFacts document.

        Returns ``None`` when the Index does not know this DID. Raises
        :class:`NandaIndexUnavailable` for connectivity/5xx failures, and
        :class:`NandaIndexInvalidResponse` for malformed or unsigned replies.
        """
        parse_did(did)  # Validate shape before making network calls.
        path = _resolve_path(did)
        try:
            response = await self._request("GET", path)
        except NandaIndexNotFound:
            return None

        body = _safe_json(response)
        facts = await self._extract_facts(body, did)
        _verify_or_raise(facts, did)
        return facts

    # ---------------------------------------------------------------- Register

    async def register(
        self,
        agent_facts: AgentFacts,
        *,
        facts_url: str | None = None,
    ) -> RegistrationResult:
        """Publish an AgentFacts document to the Index.

        ``facts_url`` is the canonical URL hosting the document (e.g. the
        MassClaw instance's ``/.well-known/agent-facts.json``). It is optional
        because some NANDA deployments accept the raw doc; others require a
        pointer. We always include both when we have them so either form is
        satisfied.
        """
        subject_did = agent_facts.credential_subject.id
        body: dict[str, Any] = {
            "did": subject_did,
            "agent_facts": agent_facts.to_document(),
        }
        if facts_url:
            body["facts_url"] = facts_url

        response = await self._request("POST", "/agents", json=body)
        raw = _safe_json(response)
        return RegistrationResult(
            did=subject_did,
            facts_url=raw.get("facts_url", facts_url),
            raw_response=raw,
        )

    async def update(
        self,
        agent_facts: AgentFacts,
        *,
        facts_url: str | None = None,
    ) -> RegistrationResult:
        """Update a previously registered AgentFacts document."""
        subject_did = agent_facts.credential_subject.id
        body: dict[str, Any] = {
            "did": subject_did,
            "agent_facts": agent_facts.to_document(),
        }
        if facts_url:
            body["facts_url"] = facts_url
        path = _resolve_path(subject_did)
        response = await self._request("PUT", path, json=body)
        raw = _safe_json(response)
        return RegistrationResult(
            did=subject_did,
            facts_url=raw.get("facts_url", facts_url),
            raw_response=raw,
        )

    async def deregister(self, did: str) -> None:
        """Remove an agent from the Index. Idempotent: 404 is treated as success."""
        parse_did(did)
        try:
            await self._request("DELETE", _resolve_path(did))
        except NandaIndexNotFound:
            return

    # ---------------------------------------------------------------- Internals

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
    ) -> httpx.Response:
        url = self._build_url(path)
        headers = self._build_headers()
        last_exc: Exception | None = None

        async with httpx.AsyncClient(
            timeout=self._config.timeout,
            transport=self._transport,
            headers=headers,
        ) as client:
            for attempt in range(1, self._config.retry_attempts + 1):
                try:
                    response = await client.request(method, url, json=json)
                except (TimeoutError, httpx.RequestError) as exc:
                    last_exc = exc
                    logger.warning(
                        "nanda_index_request_failed",
                        method=method,
                        url=url,
                        attempt=attempt,
                        error=str(exc),
                    )
                    await _sleep_backoff(attempt)
                    continue

                if response.status_code == 404:
                    raise NandaIndexNotFound(f"Index returned 404 for {method} {url}")
                if 500 <= response.status_code < 600:
                    last_exc = NandaIndexUnavailable(f"Index returned {response.status_code} for {method} {url}")
                    logger.warning(
                        "nanda_index_server_error",
                        method=method,
                        url=url,
                        attempt=attempt,
                        status=response.status_code,
                    )
                    await _sleep_backoff(attempt)
                    continue
                if response.status_code >= 400:
                    raise NandaIndexError(
                        f"Index returned {response.status_code} for {method} {url}: {response.text[:500]}"
                    )
                return response

        raise NandaIndexUnavailable(
            f"Index {method} {url} failed after {self._config.retry_attempts} attempts: {last_exc}"
        )

    async def _extract_facts(
        self,
        body: dict[str, Any],
        did: str,
    ) -> AgentFacts:
        """Accept either an embedded document or a pointer to one.

        Three acceptable shapes:
        1. Body IS the AgentFacts doc: ``{"credentialSubject": {...}, ...}``
        2. Envelope wraps it: ``{"agent_facts": {"credentialSubject": {...}}}``
        3. Pointer form: ``{"did": "...", "facts_url": "https://..."}``
        """
        if "credentialSubject" in body:
            return _parse_agent_facts(body)
        wrapped = body.get("agent_facts")
        if isinstance(wrapped, dict) and "credentialSubject" in wrapped:
            return _parse_agent_facts(wrapped)
        facts_url = body.get("facts_url") or body.get("url")
        if not isinstance(facts_url, str) or not facts_url:
            raise NandaIndexInvalidResponse(f"Index response for {did} has neither embedded document nor facts_url")
        return await self._fetch_facts_url(facts_url)

    async def _fetch_facts_url(self, url: str) -> AgentFacts:
        """Fetch a well-known AgentFacts document pointed to by the Index."""
        async with httpx.AsyncClient(
            timeout=self._config.timeout,
            transport=self._transport,
            headers=self._build_headers(),
        ) as client:
            response = await client.get(url)
        if response.status_code != 200:
            raise NandaIndexInvalidResponse(f"facts_url {url} returned HTTP {response.status_code}")
        return _parse_agent_facts(_safe_json(response))

    def _build_url(self, path: str) -> str:
        return self._config.base_url.rstrip("/") + path

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": self._config.user_agent,
            **self._config.extra_headers,
        }
        if self._config.api_token:
            headers["Authorization"] = f"Bearer {self._config.api_token}"
        return headers


# --------------------------------------------------------------------- helpers


def _resolve_path(did: str) -> str:
    # The NANDA Index uses path-escaped DIDs; percent-encoding the literal
    # ``did:nanda:...`` keeps the URL valid even when the Index is strict.
    from urllib.parse import quote

    return f"/agents/{quote(did, safe='')}"


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except Exception as exc:
        raise NandaIndexInvalidResponse(f"Index returned non-JSON body: {exc}") from exc
    if not isinstance(body, dict):
        raise NandaIndexInvalidResponse(f"Index returned non-object JSON: {type(body).__name__}")
    return body


def _parse_agent_facts(raw: dict[str, Any]) -> AgentFacts:
    try:
        return AgentFacts.model_validate(raw)
    except Exception as exc:
        raise NandaIndexInvalidResponse(f"AgentFacts schema validation failed: {exc}") from exc


def _verify_or_raise(facts: AgentFacts, expected_did: str) -> None:
    if facts.proof is None:
        raise NandaIndexInvalidResponse("Index returned AgentFacts without a proof")
    if not facts.verify():
        raise NandaIndexInvalidResponse("Index-returned AgentFacts failed signature verification")
    if facts.credential_subject.id != expected_did:
        raise NandaIndexInvalidResponse(
            f"Index returned AgentFacts for {facts.credential_subject.id!r} when we asked for {expected_did!r}"
        )


async def _sleep_backoff(attempt: int) -> None:
    # Exponential backoff with a small cap so tests stay fast.
    await asyncio.sleep(min(0.25 * (2 ** (attempt - 1)), 1.0))
