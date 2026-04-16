"""Client for the deployed MIT NANDA Index.

The real Index (``projnanda/nanda-index-frontend``) is a MongoDB-backed Node
application. Its HTTP surface:

- ``GET /api/agents`` — list all registered agents. Returns an array of
  ``{_id, username, agent_facts_link, ...}``. Resolution by handle is done
  client-side by filtering on ``username``.
- ``POST /api/agents`` — create a registration. Body:
  ``{"username": "...", "agent_facts_link": "https://.../.well-known/agent-facts.json"}``.
  Requires an authenticated Google-OAuth session cookie.
- ``PUT /api/agents/:mongo_id`` — update an existing registration; body is
  the same as POST; authenticated.
- ``DELETE /api/agents/:mongo_id`` — remove a registration; authenticated.

There is NO ``GET /agents/{did}`` — the real Index is a phonebook from
``username`` to ``agent_facts_link``, and the AgentFacts document itself is
hosted by the agent at its own ``.well-known/agent-facts.json``. We therefore
fetch the pointer from the Index and the content from the well-known URL.

Authentication: the deployed Index uses Passport + Google OAuth; headless
automation isn't supported, so operators supply a session cookie. A bearer
token escape hatch is kept for enterprise deployments that front the Index
with an API gateway.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.logging import get_logger
from app.identity.agent_facts import AgentFacts

logger = get_logger(__name__)

_DEFAULT_RETRY_ATTEMPTS = 3
_DEFAULT_USER_AGENT = "MassClaw-NANDA-Index-Client/1.0"


def _default_timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


class NandaIndexError(Exception):
    """Base class for NANDA Index client errors."""


class NandaIndexUnavailable(NandaIndexError):  # noqa: N818 — reads better than 'UnavailableError'
    """Raised when the Index is unreachable or returning 5xx after retries."""


class NandaIndexNotFound(NandaIndexError):  # noqa: N818 — mirrors FileNotFoundError
    """Raised when the Index does not know a username or a Mongo ObjectId."""


class NandaIndexInvalidResponse(NandaIndexError):  # noqa: N818 — describes the response shape
    """Raised when the Index returns a response we cannot parse."""


class NandaIndexUnauthorized(NandaIndexError):  # noqa: N818 — HTTP 401/403 surface
    """Raised when the Index rejects our credentials."""


@dataclass(frozen=True)
class NandaIndexConfig:
    """Runtime configuration for the NANDA Index client."""

    base_url: str
    timeout: httpx.Timeout = field(default_factory=_default_timeout)
    retry_attempts: int = _DEFAULT_RETRY_ATTEMPTS
    user_agent: str = _DEFAULT_USER_AGENT
    # Session cookie string as produced by the Node Passport middleware
    # (e.g. ``connect.sid=s%3A...``). Required for write operations against
    # the stock Index frontend.
    session_cookie: str | None = None
    # Bearer token escape hatch for enterprise gateways in front of the Index.
    api_token: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRegistration:
    """An entry in the NANDA Index registry."""

    mongo_id: str
    username: str
    agent_facts_link: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class RegistrationResult:
    username: str
    agent_facts_link: str
    mongo_id: str | None
    raw_response: dict[str, Any]


class NandaIndexClient:
    """HTTP client for the deployed NANDA Index registry."""

    def __init__(
        self,
        config: NandaIndexConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport  # Tests inject ``httpx.MockTransport`` here.

    # ---------------------------------------------------------------- Listing

    async def list_agents(self) -> list[AgentRegistration]:
        """Return every registration published to the Index."""
        response = await self._request("GET", "/api/agents")
        body = _safe_json_list(response)
        return [_as_registration(item) for item in body]

    async def find_by_username(self, username: str) -> AgentRegistration | None:
        """Return the registration whose ``username`` matches, or ``None``."""
        for entry in await self.list_agents():
            if entry.username == username:
                return entry
        return None

    # ---------------------------------------------------------------- Resolution

    async def resolve_by_username(self, username: str) -> AgentFacts | None:
        """Resolve a handle to a validated AgentFacts document, or ``None``.

        Returns ``None`` when the Index does not know the username. Raises
        :class:`NandaIndexUnavailable` on connectivity failures and
        :class:`NandaIndexInvalidResponse` on malformed pointers/documents.
        """
        entry = await self.find_by_username(username)
        if entry is None:
            return None
        return await self._fetch_facts_url(entry.agent_facts_link)

    # ---------------------------------------------------------------- Register / Update / Delete

    async def register(
        self,
        *,
        username: str,
        agent_facts_link: str,
    ) -> RegistrationResult:
        """Publish a pointer to an AgentFacts document.

        The body matches the real Index contract: ``{"username", "agent_facts_link"}``.
        The Index does NOT accept the full AgentFacts doc — it stores only a
        pointer. Operators must host the AgentFacts at the pointed URL.
        """
        body = {"username": username, "agent_facts_link": agent_facts_link}
        response = await self._request("POST", "/api/agents", json=body)
        raw = _safe_json(response)
        return RegistrationResult(
            username=raw.get("username", username),
            agent_facts_link=raw.get("agent_facts_link", agent_facts_link),
            mongo_id=raw.get("_id"),
            raw_response=raw,
        )

    async def update(
        self,
        mongo_id: str,
        *,
        agent_facts_link: str,
        username: str | None = None,
    ) -> RegistrationResult:
        """Update an existing registration by its Mongo ObjectId."""
        body: dict[str, Any] = {"agent_facts_link": agent_facts_link}
        if username is not None:
            body["username"] = username
        response = await self._request("PUT", f"/api/agents/{mongo_id}", json=body)
        raw = _safe_json(response)
        return RegistrationResult(
            username=raw.get("username", username or ""),
            agent_facts_link=raw.get("agent_facts_link", agent_facts_link),
            mongo_id=raw.get("_id", mongo_id),
            raw_response=raw,
        )

    async def deregister(self, mongo_id: str) -> None:
        """Remove a registration by its Mongo ObjectId. Idempotent on 404."""
        try:
            await self._request("DELETE", f"/api/agents/{mongo_id}")
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
                except (httpx.RequestError, TimeoutError) as exc:
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
                if response.status_code in (401, 403):
                    raise NandaIndexUnauthorized(f"Index refused {method} {url} with HTTP {response.status_code}")
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

    async def _fetch_facts_url(self, url: str) -> AgentFacts:
        """Fetch the AgentFacts document from the URL pointer."""
        async with httpx.AsyncClient(
            timeout=self._config.timeout,
            transport=self._transport,
            headers=self._build_headers(),
            follow_redirects=True,
        ) as client:
            try:
                response = await client.get(url)
            except httpx.RequestError as exc:
                raise NandaIndexUnavailable(f"facts_url {url} unreachable: {exc}") from exc
        if response.status_code != 200:
            raise NandaIndexInvalidResponse(f"facts_url {url} returned HTTP {response.status_code}")
        raw = _safe_json(response)
        try:
            return AgentFacts.model_validate(raw)
        except Exception as exc:
            raise NandaIndexInvalidResponse(f"AgentFacts schema validation failed: {exc}") from exc

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
        if self._config.session_cookie:
            headers["Cookie"] = self._config.session_cookie
        return headers


# ---------------------------------------------------------------- helpers


def _as_registration(raw: dict[str, Any]) -> AgentRegistration:
    mongo_id = raw.get("_id")
    username = raw.get("username")
    facts_link = raw.get("agent_facts_link") or raw.get("factsUrl")
    if not isinstance(username, str) or not username:
        raise NandaIndexInvalidResponse(f"Index entry missing username: {raw}")
    if not isinstance(facts_link, str) or not facts_link:
        raise NandaIndexInvalidResponse(f"Index entry missing agent_facts_link: {raw}")
    if not isinstance(mongo_id, str) or not mongo_id:
        raise NandaIndexInvalidResponse(f"Index entry missing _id: {raw}")
    return AgentRegistration(
        mongo_id=mongo_id,
        username=username,
        agent_facts_link=facts_link,
        raw=raw,
    )


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except Exception as exc:
        raise NandaIndexInvalidResponse(f"Index returned non-JSON body: {exc}") from exc
    if not isinstance(body, dict):
        raise NandaIndexInvalidResponse(f"Index returned non-object JSON: {type(body).__name__}")
    return body


def _safe_json_list(response: httpx.Response) -> list[dict[str, Any]]:
    try:
        body = response.json()
    except Exception as exc:
        raise NandaIndexInvalidResponse(f"Index returned non-JSON body: {exc}") from exc
    if not isinstance(body, list):
        raise NandaIndexInvalidResponse(f"Index returned non-array JSON: {type(body).__name__}")
    for item in body:
        if not isinstance(item, dict):
            raise NandaIndexInvalidResponse(f"Index returned non-object inside array: {type(item).__name__}")
    return body


async def _sleep_backoff(attempt: int) -> None:
    await asyncio.sleep(min(0.25 * (2 ** (attempt - 1)), 1.0))
