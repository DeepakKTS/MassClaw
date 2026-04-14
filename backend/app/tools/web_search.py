from __future__ import annotations

import re
from typing import Any

import httpx

from app.config import get_settings
from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult

_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_DEFAULT_SCRAPE_MAX_CHARS = 5000
_REQUEST_TIMEOUT = 15.0


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
        "Search the web using Brave Search API and return structured results. "
        "Requires BRAVE_SEARCH_API_KEY to be configured in settings."
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
        api_key = settings.brave_search_api_key
        if not api_key:
            return ToolResult(
                content="Error: BRAVE_SEARCH_API_KEY is not configured. Set it in your environment or .env file.",
                success=False,
            )

        query: str = arguments.get("query", "").strip()
        if not query:
            return ToolResult(content="Error: 'query' argument is required.", success=False)

        count: int = min(max(int(arguments.get("count", 10)), 1), 20)

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
                metadata={"status_code": exc.response.status_code},
            )
        except httpx.RequestError as exc:
            return ToolResult(
                content=f"Error: network request failed — {exc}",
                success=False,
            )

        web_results = data.get("web", {}).get("results", [])
        if not web_results:
            return ToolResult(
                content=f"No results found for query: {query!r}",
                success=True,
                metadata={"query": query, "count": 0},
            )

        lines: list[str] = [f"Search results for: {query!r}\n"]
        for i, item in enumerate(web_results, start=1):
            title = item.get("title", "(no title)")
            url = item.get("url", "")
            description = item.get("description", "")
            lines.append(f"{i}. {title}\n   URL: {url}\n   {description}\n")

        return ToolResult(
            content="\n".join(lines),
            success=True,
            metadata={"query": query, "count": len(web_results)},
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
