"""Unit tests for :class:`HttpxWellKnownFetcher`."""

from __future__ import annotations

import httpx
import pytest

from app.identity.well_known_fetcher import HttpxWellKnownFetcher, WellKnownFetchError


def _fetcher(handler) -> HttpxWellKnownFetcher:
    return HttpxWellKnownFetcher(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_https_returns_json_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hello": "world"})

    body = await _fetcher(handler).fetch("https://example.invalid/.well-known/agent-facts.json")
    assert body == {"hello": "world"}


@pytest.mark.asyncio
async def test_http_localhost_allowed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    body = await _fetcher(handler).fetch("http://localhost:8000/.well-known/agent-facts.json")
    assert body == {"ok": True}


@pytest.mark.asyncio
async def test_plain_http_to_non_localhost_rejected():
    fetcher = HttpxWellKnownFetcher()
    with pytest.raises(WellKnownFetchError):
        await fetcher.fetch("http://example.com/.well-known/agent-facts.json")


@pytest.mark.asyncio
async def test_404_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    body = await _fetcher(handler).fetch("https://example.invalid/.well-known/agent-facts.json")
    assert body is None


@pytest.mark.asyncio
async def test_500_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(WellKnownFetchError):
        await _fetcher(handler).fetch("https://example.invalid/.well-known/agent-facts.json")


@pytest.mark.asyncio
async def test_non_json_body_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    with pytest.raises(WellKnownFetchError):
        await _fetcher(handler).fetch("https://example.invalid/.well-known/agent-facts.json")


@pytest.mark.asyncio
async def test_non_object_json_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["list", "not", "object"])

    with pytest.raises(WellKnownFetchError):
        await _fetcher(handler).fetch("https://example.invalid/.well-known/agent-facts.json")


@pytest.mark.asyncio
async def test_connection_error_surfaces():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(WellKnownFetchError):
        await _fetcher(handler).fetch("https://example.invalid/.well-known/agent-facts.json")


@pytest.mark.asyncio
async def test_follows_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(302, headers={"Location": "/new"})
        return httpx.Response(200, json={"moved": True})

    body = await _fetcher(handler).fetch("https://example.invalid/old")
    assert body == {"moved": True}
