from __future__ import annotations

import re
from typing import Any

import httpx

from app.config import get_settings
from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult

_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v2/search"
_FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
_DEFAULT_SCRAPE_MAX_CHARS = 5000
_REQUEST_TIMEOUT = 30.0


def _strip_html(html: str) -> str:
    """Minimal regex-based HTML tag stripper used as BeautifulSoup fallback."""
    # Remove script / style blocks entirely
    html = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Strip all remaining tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


class WebSearchTool(ToolProvider):
    name = "web_search"
    description = (
        "Search the web and return structured results. Uses Firecrawl "
        "when FIRECRAWL_API_KEY is set (preferred), falls back to Brave when "
        "BRAVE_SEARCH_API_KEY is set."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query string.",
            },
            "count": {
                "type": "integer",
                "description": "Maximum number of results to return (1–20, default 10).",
                "default": 10,
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["query"],
    }
    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities: set[str] = {
        "research",
        "data-retrieval",
        "literature-review",
        "verification",
        "quality-check",
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        settings = get_settings()
        query: str = arguments.get("query", "").strip()
        if not query:
            return ToolResult(content="Error: 'query' argument is required.", success=False)
        count: int = min(max(int(arguments.get("count", 10)), 1), 20)

        # Prefer Firecrawl (current provider) over legacy Brave. Either
        # key enables the tool; Firecrawl wins when both are present.
        firecrawl_key = getattr(settings, "firecrawl_api_key", "") or ""
        brave_key = settings.brave_search_api_key or ""

        if firecrawl_key:
            return await self._execute_firecrawl(query=query, count=count, api_key=firecrawl_key)
        if brave_key:
            return await self._execute_brave(query=query, count=count, api_key=brave_key)
        return ToolResult(
            content=(
                "Error: no web-search provider configured. Set FIRECRAWL_API_KEY "
                "(preferred) or BRAVE_SEARCH_API_KEY in the backend environment."
            ),
            success=False,
        )

    async def _execute_firecrawl(self, *, query: str, count: int, api_key: str) -> ToolResult:
        """Firecrawl v2 search. Returns structured {title, url, description} per hit."""
        payload = {"query": query, "limit": count}
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                response = await client.post(_FIRECRAWL_SEARCH_URL, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            body_snippet = (exc.response.text or "")[:300] if exc.response is not None else ""
            return ToolResult(
                content=f"Error: Firecrawl search returned HTTP {exc.response.status_code}: {body_snippet}",
                success=False,
                metadata={"status_code": exc.response.status_code, "provider": "firecrawl"},
            )
        except httpx.RequestError as exc:
            return ToolResult(
                content=f"Error: Firecrawl network request failed — {exc}",
                success=False,
                metadata={"provider": "firecrawl"},
            )

        # Firecrawl v2 returns either {"data": {"web": [...]}} or {"data": [...]}
        raw = data.get("data") if isinstance(data, dict) else None
        if isinstance(raw, dict):
            hits = raw.get("web") or raw.get("results") or []
        elif isinstance(raw, list):
            hits = raw
        else:
            hits = []

        if not hits:
            detail = data.get("error") or data.get("message") or ""
            return ToolResult(
                content=f"No results from Firecrawl for query: {query!r}" + (f" ({detail})" if detail else ""),
                success=True,
                metadata={"query": query, "count": 0, "provider": "firecrawl"},
            )

        lines: list[str] = [f"Firecrawl results for: {query!r}\n"]
        for i, item in enumerate(hits[:count], start=1):
            title = item.get("title") or item.get("metadata", {}).get("title", "(no title)")
            url = item.get("url") or item.get("sourceURL") or ""
            description = item.get("description") or item.get("snippet") or item.get("markdown", "")[:200] or ""
            lines.append(f"{i}. {title}\n   URL: {url}\n   {description}\n")

        return ToolResult(
            content="\n".join(lines),
            success=True,
            metadata={"query": query, "count": len(hits), "provider": "firecrawl"},
        )

    async def _execute_brave(self, *, query: str, count: int, api_key: str) -> ToolResult:
        """Legacy Brave search — kept for rollback."""
        params = {"q": query, "count": count}
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                response = await client.get(_BRAVE_SEARCH_URL, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                content=f"Error: Brave Search API returned HTTP {exc.response.status_code}.",
                success=False,
                metadata={"status_code": exc.response.status_code, "provider": "brave"},
            )
        except httpx.RequestError as exc:
            return ToolResult(
                content=f"Error: Brave network request failed — {exc}",
                success=False,
                metadata={"provider": "brave"},
            )

        if "error" in data or ("message" in data and "web" not in data):
            error_msg = data.get("error", data.get("message", "Unknown API error"))
            return ToolResult(content=f"Brave search API error: {error_msg}", success=False)

        web_results = data.get("web", {}).get("results", [])
        if not web_results:
            return ToolResult(
                content=f"No Brave results for query: {query!r}",
                success=True,
                metadata={"query": query, "count": 0, "provider": "brave"},
            )

        lines: list[str] = [f"Brave results for: {query!r}\n"]
        for i, item in enumerate(web_results, start=1):
            title = item.get("title", "(no title)")
            url = item.get("url", "")
            description = item.get("description", "")
            lines.append(f"{i}. {title}\n   URL: {url}\n   {description}\n")

        return ToolResult(
            content="\n".join(lines),
            success=True,
            metadata={"query": query, "count": len(web_results), "provider": "brave"},
        )


class WebScrapeTool(ToolProvider):
    name = "web_scrape"
    description = (
        "Fetch a URL and extract the visible text content from the page. Returns up to 5000 characters by default."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch and scrape.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum number of characters to return (default 5000).",
                "default": 5000,
                "minimum": 100,
                "maximum": 50000,
            },
        },
        "required": ["url"],
    }
    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities: set[str] = {
        "research",
        "data-retrieval",
        "literature-review",
        "verification",
        "quality-check",
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        url: str = arguments.get("url", "").strip()
        if not url:
            return ToolResult(content="Error: 'url' argument is required.", success=False)

        max_chars: int = int(arguments.get("max_chars", _DEFAULT_SCRAPE_MAX_CHARS))
        max_chars = max(100, min(max_chars, 50000))

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=False) as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": "MassClaw-Agent/1.0 (research scraper)"},
                )
                response.raise_for_status()

                # Reject non-text content types (PDFs, images, binaries)
                content_type = response.headers.get("content-type", "")
                if content_type and not any(
                    ct in content_type.lower()
                    for ct in ("text/html", "text/plain", "application/json", "text/xml", "application/xml")
                ):
                    return ToolResult(
                        content=f"URL returned non-text content ({content_type}). Only HTML/text/JSON/XML can be scraped.",
                        success=False,
                        metadata={"url": url, "content_type": content_type},
                    )

                raw_html = response.text
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                content=f"Error: HTTP {exc.response.status_code} fetching {url}",
                success=False,
                metadata={"status_code": exc.response.status_code, "url": url},
            )
        except httpx.RequestError as exc:
            return ToolResult(
                content=f"Error: network request failed — {exc}",
                success=False,
                metadata={"url": url},
            )

        # Attempt BeautifulSoup first, fall back to regex stripper
        try:
            from bs4 import BeautifulSoup  # type: ignore[import-untyped]

            soup = BeautifulSoup(raw_html, "html.parser")
            # Remove script/style nodes
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
        except ImportError:
            text = _strip_html(raw_html)

        # Collapse runs of whitespace for cleanliness
        text = re.sub(r"\s+", " ", text).strip()

        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "…"

        return ToolResult(
            content=text,
            success=True,
            metadata={
                "url": url,
                "chars_returned": len(text),
                "truncated": truncated,
            },
        )
