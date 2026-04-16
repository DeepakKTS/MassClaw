"""Fetcher for ``.well-known/agent-facts.json`` documents over HTTPS.

Used as the last-resort resolution strategy when the NANDA Index does not
know a DID but the caller happens to know where the agent publishes its
AgentFacts document (e.g. via a handle embedded in an MCP tool description
or a human-provided URL).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


def _default_timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


class WellKnownFetchError(Exception):
    """Raised when a well-known AgentFacts document cannot be retrieved."""


class HttpxWellKnownFetcher:
    """Fetches ``.well-known/agent-facts.json`` over HTTPS."""

    def __init__(
        self,
        *,
        timeout: httpx.Timeout | float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        user_agent: str = "MassClaw-WellKnown-Fetcher/1.0",
    ) -> None:
        self._timeout = timeout if timeout is not None else _default_timeout()
        self._transport = transport
        self._user_agent = user_agent

    async def fetch(self, url: str) -> dict[str, Any] | None:
        """Fetch a JSON document. Returns ``None`` on 404; raises on other errors.

        Only ``https://`` and ``http://localhost`` / ``http://127.0.0.1`` are
        considered; plain ``http://`` to arbitrary hosts is rejected to avoid
        accidental downgrade attacks.
        """
        if not _is_safe_url(url):
            raise WellKnownFetchError(f"refusing to fetch {url!r}: only https:// or localhost http:// is allowed")

        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
            headers={"Accept": "application/json", "User-Agent": self._user_agent},
            follow_redirects=True,
        ) as client:
            try:
                response = await client.get(url)
            except httpx.RequestError as exc:
                raise WellKnownFetchError(f"connection to {url} failed: {exc}") from exc

        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise WellKnownFetchError(f"{url} returned HTTP {response.status_code}: {response.text[:500]}")
        try:
            body = response.json()
        except Exception as exc:
            raise WellKnownFetchError(f"{url} returned non-JSON body: {exc}") from exc
        if not isinstance(body, dict):
            raise WellKnownFetchError(f"{url} returned non-object JSON: {type(body).__name__}")
        return body


def _is_safe_url(url: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme == "https":
        return True
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return True
    return False
