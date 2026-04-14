from __future__ import annotations

import ipaddress
import socket
from typing import Any

import httpx

from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult

_REQUEST_TIMEOUT = 15.0
_RESPONSE_CAP = 10_000

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # unique local IPv6
    ipaddress.ip_network("fe80::/10"),  # link-local IPv6
]

_BLOCKED_HOSTNAMES = {"localhost", "0.0.0.0"}

_ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}


def _is_private_url(url: str) -> bool:
    """Return True if *url* resolves to a private / loopback address (SSRF guard)."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        hostname = parsed.hostname or ""
    except Exception:
        return True  # Treat unparseable URLs as private (safe default)

    if not hostname:
        return True

    if hostname.lower() in _BLOCKED_HOSTNAMES:
        return True

    # Resolve hostname to IP addresses and check each one
    try:
        addrinfos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # Cannot resolve → treat as safe (will fail at request time)
        return False

    for addrinfo in addrinfos:
        ip_str = addrinfo[4][0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for network in _PRIVATE_NETWORKS:
            if ip_obj in network:
                return True

    return False


class APICallerTool(ToolProvider):
    name = "api_call"
    description = (
        "Make HTTP requests (GET, POST, PUT, DELETE, PATCH) to external APIs. "
        "Private/internal network addresses are blocked for security. "
        "Responses are capped at 10 000 characters."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full URL of the API endpoint.",
            },
            "method": {
                "type": "string",
                "description": "HTTP method: GET, POST, PUT, DELETE, or PATCH (default: GET).",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                "default": "GET",
            },
            "headers": {
                "type": "object",
                "description": "Optional HTTP headers as key-value pairs.",
                "additionalProperties": {"type": "string"},
            },
            "body": {
                "type": "object",
                "description": "Optional JSON request body (for POST/PUT/PATCH).",
            },
            "params": {
                "type": "object",
                "description": "Optional query string parameters as key-value pairs.",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["url"],
    }
    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities: set[str] = {
        "process-analysis",
        "cost-analysis",
        "budget-estimation",
        "workflow-mapping",
        "code-execution",
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        from urllib.parse import urlparse

        url: str = arguments.get("url", "").strip()
        if not url:
            return ToolResult(content="Error: 'url' argument is required.", success=False)

        # Scheme validation — block file://, ftp://, etc.
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ToolResult(
                content=f"Blocked: only http/https URLs allowed (got: {parsed.scheme or 'none'})",
                success=False,
            )

        method: str = arguments.get("method", "GET").upper()
        if method not in _ALLOWED_METHODS:
            return ToolResult(
                content=f"Error: unsupported HTTP method '{method}'. Allowed: {', '.join(sorted(_ALLOWED_METHODS))}.",
                success=False,
            )

        # SSRF protection — resolve before connecting
        if _is_private_url(url):
            return ToolResult(
                content=f"Error: requests to private or internal network addresses are not permitted. URL: {url}",
                success=False,
                metadata={"url": url, "blocked": True},
            )

        headers: dict[str, str] = arguments.get("headers") or {}
        body: dict[str, Any] | None = arguments.get("body")
        params: dict[str, str] | None = arguments.get("params")

        try:
            async with httpx.AsyncClient(
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=False,  # Disabled to prevent SSRF via redirect chains
            ) as client:
                request_kwargs: dict[str, Any] = {
                    "headers": headers,
                }
                if params:
                    request_kwargs["params"] = params
                if body is not None:
                    request_kwargs["json"] = body

                response = await client.request(method, url, **request_kwargs)
                status_code = response.status_code
                response_text = response.text
        except httpx.TimeoutException:
            return ToolResult(
                content=f"Error: request to {url} timed out after {_REQUEST_TIMEOUT}s.",
                success=False,
                metadata={"url": url, "timeout": _REQUEST_TIMEOUT},
            )
        except httpx.RequestError as exc:
            return ToolResult(
                content=f"Error: network request failed — {exc}",
                success=False,
                metadata={"url": url},
            )

        truncated = len(response_text) > _RESPONSE_CAP
        body_out = response_text[:_RESPONSE_CAP] + ("…" if truncated else "")

        success = 200 <= status_code < 300
        content = f"HTTP {status_code}\n\n{body_out}"

        return ToolResult(
            content=content,
            success=success,
            metadata={
                "url": url,
                "method": method,
                "status_code": status_code,
                "truncated": truncated,
                "response_chars": len(body_out),
            },
        )
