# Tool Use System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give MassClaw agents the ability to use tools — browse the web, execute code, read/write files, and call APIs — with safety, cost tracking, and audit logging.

**Architecture:** Pluggable ToolProvider interface with 7 built-in tools. Hybrid execution (in-process for lightweight, Docker sandbox for code). Agentic loop in scheduler: LLM returns tool_calls → execute → feed results back → repeat (max 5 iterations). Capability-based permissions map agent capabilities to allowed tools.

**Tech Stack:** FastAPI, httpx, aiodocker, beautifulsoup4, existing Anthropic/OpenAI SDK tool_use support

**Spec:** `docs/superpowers/specs/2026-04-14-tool-use-system-design.md`

---

## File Structure

```
NEW FILES:
  backend/app/tools/__init__.py          — Package docstring
  backend/app/tools/base.py              — ToolProvider ABC, ToolResult, ToolContext, ExecutionMode
  backend/app/tools/registry.py          — ToolRegistry singleton, capability mapping
  backend/app/tools/executor.py          — ToolExecutor service (safety + audit + cost)
  backend/app/tools/file_io.py           — FileReadTool, FileWriteTool, FileListTool
  backend/app/tools/web_search.py        — WebSearchTool, WebScrapeTool
  backend/app/tools/api_caller.py        — APICallerTool
  backend/app/tools/code_execution.py    — CodeExecutionTool (Docker sandbox)
  backend/app/api/tools.py               — GET/POST /api/v1/tools endpoints
  backend/tests/unit/test_tool_base.py   — Tests for base classes
  backend/tests/unit/test_tool_registry.py — Tests for registry + capability mapping
  backend/tests/unit/test_tool_executor.py — Tests for executor safety/audit
  backend/tests/unit/test_tools_builtin.py — Tests for built-in tool implementations

MODIFIED FILES:
  backend/app/config.py                  — Add tool settings
  backend/app/exceptions.py              — Add ToolExecutionError
  backend/app/models/base.py             — Add TOOL_* audit event types
  backend/app/orchestration/scheduler.py — Agentic loop in _llm_call()
  backend/app/dependencies.py            — Add get_tool_executor()
  backend/app/api/router.py              — Mount /tools router
  backend/app/main.py                    — Init tool registry in lifespan
  backend/pyproject.toml                 — Add aiodocker, beautifulsoup4
  backend/.env.example (root)            — Document tool settings
```

---

### Task 1: Foundation — Base Classes and Config

**Files:**
- Create: `backend/app/tools/__init__.py`
- Create: `backend/app/tools/base.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/exceptions.py`
- Modify: `backend/app/models/base.py`
- Test: `backend/tests/unit/test_tool_base.py`

- [ ] **Step 1: Write failing test for base classes**

Create `backend/tests/unit/test_tool_base.py`:

```python
from __future__ import annotations

import uuid

import pytest

from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult


class TestToolResult:
    def test_create_success_result(self):
        result = ToolResult(content="Hello", success=True, metadata={}, artifacts=[])
        assert result.success is True
        assert result.content == "Hello"
        assert result.cost_credits == 0.0

    def test_create_failure_result(self):
        result = ToolResult(content="Error", success=False, metadata={"error": "timeout"}, artifacts=[])
        assert result.success is False


class TestToolContext:
    def test_create_context(self):
        ctx = ToolContext(
            workflow_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            workspace_path="/tmp/test",
        )
        assert ctx.timeout_seconds == 30
        assert ctx.max_iterations == 5


class TestToolProvider:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            ToolProvider()  # type: ignore[abstract]

    def test_to_schema(self):
        class FakeTool(ToolProvider):
            name = "fake"
            description = "A fake tool"
            parameters_schema = {"type": "object", "properties": {"q": {"type": "string"}}}
            execution_mode = ExecutionMode.IN_PROCESS
            required_capabilities = {"research"}
            estimated_cost_credits = 0.1

            async def execute(self, arguments, context):
                return ToolResult(content="ok", success=True, metadata={}, artifacts=[])

        tool = FakeTool()
        schema = tool.to_schema()
        assert schema["name"] == "fake"
        assert schema["description"] == "A fake tool"
        assert "properties" in schema["input_schema"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/unit/test_tool_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.tools'`

- [ ] **Step 3: Create package and base module**

Create `backend/app/tools/__init__.py`:
```python
"""MassClaw Tool Use System."""
```

Create `backend/app/tools/base.py`:
```python
from __future__ import annotations

import enum
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ExecutionMode(str, enum.Enum):
    IN_PROCESS = "in_process"
    SANDBOXED = "sandboxed"


@dataclass
class ToolContext:
    workflow_id: uuid.UUID
    agent_id: uuid.UUID
    workspace_path: str
    timeout_seconds: int = 30
    max_iterations: int = 5


@dataclass
class ToolResult:
    content: str
    success: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    cost_credits: float = 0.0


class ToolProvider(ABC):
    """Abstract base class for all MassClaw tools (built-in and third-party)."""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    execution_mode: ExecutionMode
    required_capabilities: set[str]
    estimated_cost_credits: float = 0.0

    @abstractmethod
    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        """Execute the tool with the given arguments."""

    def to_schema(self) -> dict[str, Any]:
        """Return the tool schema in the format expected by LLM providers."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters_schema,
        }
```

- [ ] **Step 4: Add tool config settings**

Add to `backend/app/config.py` after the `llm_max_retries` line (~line 42):

```python
    # Tool System
    tool_max_iterations: int = 5
    tool_code_timeout_seconds: int = 30
    tool_code_memory_mb: int = 256
    tool_workspace_base: str = "/tmp/massclaw/workspaces"
    brave_search_api_key: str = ""
    tool_docker_image: str = "python:3.12-slim"
```

- [ ] **Step 5: Add ToolExecutionError**

Add to `backend/app/exceptions.py` after `OrchestrationError`:

```python
class ToolExecutionError(MassClawError):
    status_code = 500
    error_code = "TOOL_EXECUTION_ERROR"
```

- [ ] **Step 6: Add audit event types**

Add to `AuditEventType` enum in `backend/app/models/base.py`:

```python
    # Tool execution
    TOOL_EXECUTED = "tool.executed"
    TOOL_BLOCKED = "tool.blocked"
```

- [ ] **Step 7: Run tests — verify pass**

Run: `cd backend && pytest tests/unit/test_tool_base.py -v`
Expected: ALL PASS

- [ ] **Step 8: Lint and commit**

```bash
cd backend && ruff check app/tools/ && ruff format app/tools/
git add app/tools/ app/config.py app/exceptions.py app/models/base.py tests/unit/test_tool_base.py
git commit -m "feat(tools): add ToolProvider base classes, config, and exceptions"
```

---

### Task 2: Built-in Tools — File I/O

**Files:**
- Create: `backend/app/tools/file_io.py`
- Test: `backend/tests/unit/test_tools_builtin.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/unit/test_tools_builtin.py`:

```python
from __future__ import annotations

import os
import uuid

import pytest

from app.tools.base import ToolContext
from app.tools.file_io import FileListTool, FileReadTool, FileWriteTool


@pytest.fixture
def workspace(tmp_path):
    return str(tmp_path)


@pytest.fixture
def tool_context(workspace):
    return ToolContext(
        workflow_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        workspace_path=workspace,
    )


class TestFileWriteTool:
    @pytest.mark.asyncio
    async def test_write_file(self, tool_context, workspace):
        tool = FileWriteTool()
        result = await tool.execute({"path": "test.txt", "content": "Hello world"}, tool_context)
        assert result.success is True
        assert os.path.exists(os.path.join(workspace, "test.txt"))
        with open(os.path.join(workspace, "test.txt")) as f:
            assert f.read() == "Hello world"

    @pytest.mark.asyncio
    async def test_reject_path_traversal(self, tool_context):
        tool = FileWriteTool()
        result = await tool.execute({"path": "../../../etc/passwd", "content": "hacked"}, tool_context)
        assert result.success is False
        assert "traversal" in result.content.lower() or "outside" in result.content.lower()


class TestFileReadTool:
    @pytest.mark.asyncio
    async def test_read_existing_file(self, tool_context, workspace):
        filepath = os.path.join(workspace, "data.txt")
        with open(filepath, "w") as f:
            f.write("test content")
        tool = FileReadTool()
        result = await tool.execute({"path": "data.txt"}, tool_context)
        assert result.success is True
        assert "test content" in result.content

    @pytest.mark.asyncio
    async def test_read_nonexistent(self, tool_context):
        tool = FileReadTool()
        result = await tool.execute({"path": "nope.txt"}, tool_context)
        assert result.success is False


class TestFileListTool:
    @pytest.mark.asyncio
    async def test_list_empty_workspace(self, tool_context, workspace):
        tool = FileListTool()
        result = await tool.execute({}, tool_context)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_list_with_files(self, tool_context, workspace):
        open(os.path.join(workspace, "a.txt"), "w").close()
        open(os.path.join(workspace, "b.txt"), "w").close()
        tool = FileListTool()
        result = await tool.execute({}, tool_context)
        assert result.success is True
        assert "a.txt" in result.content
        assert "b.txt" in result.content
```

- [ ] **Step 2: Run tests — verify fail**

Run: `cd backend && pytest tests/unit/test_tools_builtin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.tools.file_io'`

- [ ] **Step 3: Implement file_io.py**

Create `backend/app/tools/file_io.py`:

```python
from __future__ import annotations

import os
from typing import Any

from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult


def _safe_resolve(workspace: str, relative_path: str) -> str | None:
    """Resolve a relative path within the workspace. Returns None if path escapes."""
    os.makedirs(workspace, exist_ok=True)
    resolved = os.path.realpath(os.path.join(workspace, relative_path))
    if not resolved.startswith(os.path.realpath(workspace)):
        return None
    return resolved


class FileReadTool(ToolProvider):
    name = "file_read"
    description = "Read the contents of a file from the workflow workspace."
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path within the workspace"},
        },
        "required": ["path"],
    }
    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities = {
        "research", "data-retrieval", "process-analysis", "cost-analysis",
        "verification", "quality-check", "code-execution", "optimization",
        "data-analytics", "workflow-mapping", "budget-estimation",
    }
    estimated_cost_credits = 0.0

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path = arguments.get("path", "")
        resolved = _safe_resolve(context.workspace_path, path)
        if resolved is None:
            return ToolResult(content=f"Blocked: path '{path}' is outside the workspace.", success=False)
        if not os.path.exists(resolved):
            return ToolResult(content=f"File not found: {path}", success=False)
        try:
            with open(resolved) as f:
                content = f.read(1_000_000)  # Cap at 1MB
            return ToolResult(content=content, success=True, metadata={"path": path, "size": len(content)})
        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", success=False)


class FileWriteTool(ToolProvider):
    name = "file_write"
    description = "Write content to a file in the workflow workspace."
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path within the workspace"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    }
    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities = {
        "code-execution", "optimization", "data-analytics",
        "summarization", "report-generation", "process-analysis", "workflow-mapping",
    }
    estimated_cost_credits = 0.0

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        resolved = _safe_resolve(context.workspace_path, path)
        if resolved is None:
            return ToolResult(content=f"Blocked: path '{path}' is outside the workspace.", success=False)
        try:
            os.makedirs(os.path.dirname(resolved), exist_ok=True)
            with open(resolved, "w") as f:
                f.write(content)
            return ToolResult(
                content=f"Written {len(content)} bytes to {path}",
                success=True,
                metadata={"path": path, "size": len(content)},
                artifacts=[resolved],
            )
        except Exception as e:
            return ToolResult(content=f"Error writing file: {e}", success=False)


class FileListTool(ToolProvider):
    name = "file_list"
    description = "List files in the workflow workspace directory."
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "subdirectory": {"type": "string", "description": "Optional subdirectory to list", "default": "."},
        },
    }
    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities = {
        "research", "code-execution", "process-analysis", "verification",
        "data-analytics", "workflow-mapping",
    }
    estimated_cost_credits = 0.0

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        subdir = arguments.get("subdirectory", ".")
        resolved = _safe_resolve(context.workspace_path, subdir)
        if resolved is None:
            return ToolResult(content="Blocked: path is outside the workspace.", success=False)
        if not os.path.isdir(resolved):
            return ToolResult(content=f"Directory not found: {subdir}", success=False)
        try:
            entries = os.listdir(resolved)
            listing = "\n".join(sorted(entries)) if entries else "(empty)"
            return ToolResult(content=listing, success=True, metadata={"count": len(entries)})
        except Exception as e:
            return ToolResult(content=f"Error listing directory: {e}", success=False)
```

- [ ] **Step 4: Run tests — verify pass**

Run: `cd backend && pytest tests/unit/test_tools_builtin.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add app/tools/file_io.py tests/unit/test_tools_builtin.py
git commit -m "feat(tools): add file_read, file_write, file_list tools with path traversal prevention"
```

---

### Task 3: Built-in Tools — Web Search & Scrape

**Files:**
- Create: `backend/app/tools/web_search.py`
- Modify: `backend/tests/unit/test_tools_builtin.py`

- [ ] **Step 1: Add web search tests**

Append to `backend/tests/unit/test_tools_builtin.py`:

```python
from unittest.mock import AsyncMock, patch

from app.tools.web_search import WebScrapeTool, WebSearchTool


class TestWebSearchTool:
    @pytest.mark.asyncio
    async def test_search_no_api_key(self, tool_context):
        tool = WebSearchTool()
        with patch("app.tools.web_search.get_settings") as mock_settings:
            mock_settings.return_value.brave_search_api_key = ""
            result = await tool.execute({"query": "test"}, tool_context)
            assert result.success is False
            assert "api key" in result.content.lower()

    @pytest.mark.asyncio
    async def test_search_success(self, tool_context):
        tool = WebSearchTool()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "web": {"results": [{"title": "Result 1", "url": "https://example.com", "description": "A description"}]}
        }
        with (
            patch("app.tools.web_search.get_settings") as mock_settings,
            patch("app.tools.web_search.httpx.AsyncClient") as mock_client,
        ):
            mock_settings.return_value.brave_search_api_key = "test-key"
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)
            result = await tool.execute({"query": "test"}, tool_context)
            assert result.success is True
            assert "Result 1" in result.content


class TestWebScrapeTool:
    @pytest.mark.asyncio
    async def test_scrape_success(self, tool_context):
        tool = WebScrapeTool()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><p>Hello world</p></body></html>"
        with patch("app.tools.web_search.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)
            result = await tool.execute({"url": "https://example.com"}, tool_context)
            assert result.success is True
            assert "Hello world" in result.content
```

- [ ] **Step 2: Implement web_search.py**

Create `backend/app/tools/web_search.py`:

```python
from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult


class WebSearchTool(ToolProvider):
    name = "web_search"
    description = "Search the web using Brave Search API. Returns top results with titles, URLs, and descriptions."
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {"type": "integer", "description": "Number of results (max 10)", "default": 5},
        },
        "required": ["query"],
    }
    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities = {"research", "data-retrieval", "literature-review", "verification", "quality-check"}
    estimated_cost_credits = 0.5

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        settings = get_settings()
        if not settings.brave_search_api_key:
            return ToolResult(content="Web search unavailable: no Brave Search API key configured.", success=False)

        query = arguments.get("query", "")
        count = min(arguments.get("count", 5), 10)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": count},
                    headers={"X-Subscription-Token": settings.brave_search_api_key, "Accept": "application/json"},
                )
            if resp.status_code != 200:
                return ToolResult(content=f"Search API error: HTTP {resp.status_code}", success=False)

            data = resp.json()
            results = data.get("web", {}).get("results", [])
            if not results:
                return ToolResult(content=f"No results found for: {query}", success=True, metadata={"count": 0})

            lines = []
            for i, r in enumerate(results[:count], 1):
                lines.append(f"{i}. **{r.get('title', 'Untitled')}**")
                lines.append(f"   URL: {r.get('url', '')}")
                lines.append(f"   {r.get('description', 'No description')}")
                lines.append("")
            return ToolResult(
                content="\n".join(lines), success=True, metadata={"query": query, "count": len(results)}
            )
        except Exception as e:
            return ToolResult(content=f"Search failed: {e}", success=False)


class WebScrapeTool(ToolProvider):
    name = "web_scrape"
    description = "Fetch and extract text content from a web page URL."
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to scrape"},
            "max_chars": {"type": "integer", "description": "Max characters to return", "default": 5000},
        },
        "required": ["url"],
    }
    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities = {"research", "data-retrieval", "literature-review"}
    estimated_cost_credits = 0.3

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        url = arguments.get("url", "")
        max_chars = min(arguments.get("max_chars", 5000), 20000)

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "MassClaw/1.0"})
            if resp.status_code != 200:
                return ToolResult(content=f"Failed to fetch URL: HTTP {resp.status_code}", success=False)

            # Extract text — try BeautifulSoup if available, fallback to basic
            try:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
            except ImportError:
                # Fallback: strip HTML tags with basic regex
                import re

                text = re.sub(r"<[^>]+>", "", resp.text)
                text = re.sub(r"\s+", " ", text).strip()

            content = text[:max_chars]
            return ToolResult(content=content, success=True, metadata={"url": url, "chars": len(content)})
        except Exception as e:
            return ToolResult(content=f"Scrape failed: {e}", success=False)
```

- [ ] **Step 3: Run tests — verify pass**

Run: `cd backend && pytest tests/unit/test_tools_builtin.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add app/tools/web_search.py tests/unit/test_tools_builtin.py
git commit -m "feat(tools): add web_search and web_scrape tools with Brave Search API"
```

---

### Task 4: Built-in Tools — API Caller

**Files:**
- Create: `backend/app/tools/api_caller.py`
- Modify: `backend/tests/unit/test_tools_builtin.py`

- [ ] **Step 1: Add API caller tests**

Append to `backend/tests/unit/test_tools_builtin.py`:

```python
from app.tools.api_caller import APICallerTool


class TestAPICallerTool:
    @pytest.mark.asyncio
    async def test_block_private_ip(self, tool_context):
        tool = APICallerTool()
        result = await tool.execute({"url": "http://127.0.0.1/admin", "method": "GET"}, tool_context)
        assert result.success is False
        assert "blocked" in result.content.lower()

    @pytest.mark.asyncio
    async def test_block_private_network(self, tool_context):
        tool = APICallerTool()
        result = await tool.execute({"url": "http://192.168.1.1/", "method": "GET"}, tool_context)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_success(self, tool_context):
        tool = APICallerTool()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = '{"data": "ok"}'
        mock_response.headers = {"content-type": "application/json"}
        with patch("app.tools.api_caller.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.request = AsyncMock(return_value=mock_response)
            result = await tool.execute({"url": "https://api.example.com/data", "method": "GET"}, tool_context)
            assert result.success is True
```

- [ ] **Step 2: Implement api_caller.py**

Create `backend/app/tools/api_caller.py`:

```python
from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

import httpx

from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
]


def _is_private_url(url: str) -> bool:
    """Check if a URL resolves to a private/local IP address."""
    try:
        hostname = urlparse(url).hostname
        if not hostname:
            return True
        if hostname in ("localhost", "0.0.0.0"):
            return True
        import socket

        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _, _, _, _, addr in resolved:
            ip = ipaddress.ip_address(addr[0])
            if any(ip in net for net in _BLOCKED_NETWORKS) or ip.is_private:
                return True
    except Exception:
        return True  # Block on resolution failure
    return False


class APICallerTool(ToolProvider):
    name = "api_call"
    description = "Make an HTTP request to an external API. Supports GET, POST, PUT, DELETE with headers and body."
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to call"},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE"], "default": "GET"},
            "headers": {"type": "object", "description": "HTTP headers", "default": {}},
            "body": {"type": "string", "description": "Request body (for POST/PUT)"},
        },
        "required": ["url", "method"],
    }
    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities = {"process-analysis", "cost-analysis", "budget-estimation", "workflow-mapping", "code-execution"}
    estimated_cost_credits = 0.2

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        url = arguments.get("url", "")
        method = arguments.get("method", "GET").upper()
        headers = arguments.get("headers", {})
        body = arguments.get("body")

        if _is_private_url(url):
            return ToolResult(content=f"Blocked: URL '{url}' resolves to a private/local address (SSRF protection).", success=False)

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.request(method, url, headers=headers, content=body)
            content = resp.text[:10000]
            return ToolResult(
                content=content,
                success=True,
                metadata={"status_code": resp.status_code, "url": url, "method": method},
            )
        except Exception as e:
            return ToolResult(content=f"API call failed: {e}", success=False)
```

- [ ] **Step 3: Run tests — verify pass**

Run: `cd backend && pytest tests/unit/test_tools_builtin.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add app/tools/api_caller.py tests/unit/test_tools_builtin.py
git commit -m "feat(tools): add api_caller tool with SSRF protection"
```

---

### Task 5: Built-in Tools — Code Execution (Docker Sandbox)

**Files:**
- Create: `backend/app/tools/code_execution.py`
- Modify: `backend/tests/unit/test_tools_builtin.py`
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add aiodocker + beautifulsoup4 dependencies**

Add to `pyproject.toml` dependencies:
```toml
    "aiodocker>=0.23,<1.0",
    "beautifulsoup4>=4.12,<5.0",
```

Run: `cd backend && pip install ".[dev]"`

- [ ] **Step 2: Add code execution tests**

Append to `backend/tests/unit/test_tools_builtin.py`:

```python
from app.tools.code_execution import CodeExecutionTool


class TestCodeExecutionTool:
    @pytest.mark.asyncio
    async def test_schema(self):
        tool = CodeExecutionTool()
        assert tool.name == "code_execute"
        assert tool.execution_mode == ExecutionMode.SANDBOXED
        schema = tool.to_schema()
        assert "language" in str(schema["input_schema"]["properties"])

    @pytest.mark.asyncio
    async def test_reject_empty_code(self, tool_context):
        tool = CodeExecutionTool()
        result = await tool.execute({"language": "python", "code": ""}, tool_context)
        assert result.success is False
```

- [ ] **Step 3: Implement code_execution.py**

Create `backend/app/tools/code_execution.py`:

```python
from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.core.logging import get_logger
from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult

logger = get_logger(__name__)


class CodeExecutionTool(ToolProvider):
    name = "code_execute"
    description = (
        "Execute code in a sandboxed Docker container. "
        "Supports Python, JavaScript (Node.js), and shell scripts. "
        "Returns stdout, stderr, and exit code."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "enum": ["python", "javascript", "shell"],
                "description": "Programming language",
            },
            "code": {"type": "string", "description": "Code to execute"},
        },
        "required": ["language", "code"],
    }
    execution_mode = ExecutionMode.SANDBOXED
    required_capabilities = {"code-execution", "optimization", "data-analytics"}
    estimated_cost_credits = 2.0

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        language = arguments.get("language", "python")
        code = arguments.get("code", "")

        if not code.strip():
            return ToolResult(content="No code provided.", success=False)

        settings = get_settings()
        timeout = settings.tool_code_timeout_seconds
        memory_mb = settings.tool_code_memory_mb

        try:
            import aiodocker
        except ImportError:
            return ToolResult(content="Code execution unavailable: aiodocker not installed.", success=False)

        cmd_map = {
            "python": ["python3", "-c", code],
            "javascript": ["node", "-e", code],
            "shell": ["sh", "-c", code],
        }
        cmd = cmd_map.get(language)
        if cmd is None:
            return ToolResult(content=f"Unsupported language: {language}", success=False)

        image = settings.tool_docker_image
        container_name = f"massclaw-exec-{context.workflow_id.hex[:8]}-{language}"

        try:
            docker = aiodocker.Docker()
            try:
                container = await docker.containers.create_or_replace(
                    name=container_name,
                    config={
                        "Image": image,
                        "Cmd": cmd,
                        "HostConfig": {
                            "Memory": memory_mb * 1024 * 1024,
                            "NetworkMode": "none",
                            "ReadonlyRootfs": False,
                        },
                        "WorkingDir": "/tmp",
                    },
                )
                await container.start()

                import asyncio

                try:
                    result = await asyncio.wait_for(container.wait(), timeout=timeout)
                    exit_code = result.get("StatusCode", -1)
                except TimeoutError:
                    await container.kill()
                    return ToolResult(content=f"Code execution timed out after {timeout}s.", success=False)

                logs = await container.log(stdout=True, stderr=True)
                output = "".join(logs)[:10000]

                return ToolResult(
                    content=output if output else "(no output)",
                    success=exit_code == 0,
                    metadata={"language": language, "exit_code": exit_code},
                )
            finally:
                try:
                    await container.delete(force=True)
                except Exception:
                    pass
                await docker.close()
        except Exception as e:
            logger.error("code_execution_failed", error=str(e))
            return ToolResult(content=f"Docker execution failed: {e}", success=False)
```

- [ ] **Step 4: Run tests — verify pass**

Run: `cd backend && pytest tests/unit/test_tools_builtin.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add app/tools/code_execution.py pyproject.toml tests/unit/test_tools_builtin.py
git commit -m "feat(tools): add code_execute tool with Docker sandbox"
```

---

### Task 6: Tool Registry

**Files:**
- Create: `backend/app/tools/registry.py`
- Test: `backend/tests/unit/test_tool_registry.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/unit/test_tool_registry.py`:

```python
from __future__ import annotations

import pytest

from app.tools.registry import ToolRegistry


class TestToolRegistry:
    def test_built_in_tools_registered(self):
        registry = ToolRegistry()
        tools = registry.list_tools()
        names = {t.name for t in tools}
        assert "web_search" in names
        assert "file_read" in names
        assert "file_write" in names
        assert "file_list" in names
        assert "web_scrape" in names
        assert "api_call" in names
        assert "code_execute" in names

    def test_get_tool_by_name(self):
        registry = ToolRegistry()
        tool = registry.get("web_search")
        assert tool is not None
        assert tool.name == "web_search"

    def test_get_nonexistent_returns_none(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_get_tools_for_research_capabilities(self):
        registry = ToolRegistry()
        tools = registry.get_tools_for_capabilities(["research"])
        names = {t.name for t in tools}
        assert "web_search" in names
        assert "web_scrape" in names
        assert "file_read" in names
        assert "code_execute" not in names

    def test_get_tools_for_code_execution(self):
        registry = ToolRegistry()
        tools = registry.get_tools_for_capabilities(["code-execution"])
        names = {t.name for t in tools}
        assert "code_execute" in names
        assert "file_read" in names
        assert "file_write" in names

    def test_get_tools_for_text_only_capability(self):
        registry = ToolRegistry()
        tools = registry.get_tools_for_capabilities(["intake"])
        assert len(tools) == 0

    def test_to_schemas(self):
        registry = ToolRegistry()
        tools = registry.get_tools_for_capabilities(["research"])
        schemas = [t.to_schema() for t in tools]
        assert all("name" in s and "input_schema" in s for s in schemas)
```

- [ ] **Step 2: Implement registry.py**

Create `backend/app/tools/registry.py`:

```python
from __future__ import annotations

from app.core.logging import get_logger
from app.tools.base import ToolProvider

logger = get_logger(__name__)

_registry: ToolRegistry | None = None


class ToolRegistry:
    """Singleton registry for all available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolProvider] = {}
        self._load_built_in_tools()

    def _load_built_in_tools(self) -> None:
        from app.tools.api_caller import APICallerTool
        from app.tools.code_execution import CodeExecutionTool
        from app.tools.file_io import FileListTool, FileReadTool, FileWriteTool
        from app.tools.web_search import WebScrapeTool, WebSearchTool

        built_ins: list[ToolProvider] = [
            WebSearchTool(),
            WebScrapeTool(),
            CodeExecutionTool(),
            FileReadTool(),
            FileWriteTool(),
            FileListTool(),
            APICallerTool(),
        ]
        for tool in built_ins:
            self.register(tool)
        logger.info("tool_registry_initialized", count=len(self._tools))

    def register(self, tool: ToolProvider) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolProvider | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolProvider]:
        return list(self._tools.values())

    def get_tools_for_capabilities(self, capabilities: list[str]) -> list[ToolProvider]:
        """Return tools accessible by an agent with the given capabilities."""
        cap_set = set(capabilities)
        return [t for t in self._tools.values() if t.required_capabilities & cap_set]


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
```

- [ ] **Step 3: Run tests — verify pass**

Run: `cd backend && pytest tests/unit/test_tool_registry.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add app/tools/registry.py tests/unit/test_tool_registry.py
git commit -m "feat(tools): add ToolRegistry with capability-based tool discovery"
```

---

### Task 7: Tool Executor (Safety + Audit + Cost)

**Files:**
- Create: `backend/app/tools/executor.py`
- Test: `backend/tests/unit/test_tool_executor.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/unit/test_tool_executor.py`:

```python
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult
from app.tools.executor import ToolExecutor


class FakeTool(ToolProvider):
    name = "fake_tool"
    description = "A fake tool for testing"
    parameters_schema = {"type": "object", "properties": {"input": {"type": "string"}}}
    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities = {"research"}
    estimated_cost_credits = 1.0

    async def execute(self, arguments, context):
        return ToolResult(content=f"Result: {arguments.get('input', '')}", success=True)


@pytest.fixture
def tool_context():
    return ToolContext(workflow_id=uuid.uuid4(), agent_id=uuid.uuid4(), workspace_path="/tmp/test")


@pytest.fixture
def executor():
    session = AsyncMock()
    redis = AsyncMock()
    return ToolExecutor(session=session, redis=redis)


class TestToolExecutor:
    @pytest.mark.asyncio
    async def test_execute_success(self, executor, tool_context):
        with patch("app.tools.executor.get_tool_registry") as mock_registry:
            mock_registry.return_value.get.return_value = FakeTool()
            result = await executor.execute_tool("fake_tool", {"input": "hello"}, tool_context)
            assert result.success is True
            assert "hello" in result.content

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self, executor, tool_context):
        with patch("app.tools.executor.get_tool_registry") as mock_registry:
            mock_registry.return_value.get.return_value = None
            result = await executor.execute_tool("nonexistent", {}, tool_context)
            assert result.success is False
            assert "not found" in result.content.lower()
```

- [ ] **Step 2: Implement executor.py**

Create `backend/app/tools/executor.py`:

```python
from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.tools.base import ToolContext, ToolResult
from app.tools.registry import get_tool_registry

logger = get_logger(__name__)


class ToolExecutor:
    """Executes tools with safety validation, content filtering, audit logging, and cost tracking."""

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        registry = get_tool_registry()
        tool = registry.get(tool_name)
        if tool is None:
            return ToolResult(content=f"Tool not found: {tool_name}", success=False)

        # Pre-execution safety: check arguments for injection
        try:
            from app.safety.injection_detector import InjectionDetector

            detector = InjectionDetector()
            assessment = await detector.analyze(str(arguments))
            if assessment.is_suspicious and assessment.recommendation == "block":
                logger.warning(
                    "tool_execution_blocked",
                    tool=tool_name,
                    risk_score=assessment.risk_score,
                )
                return ToolResult(
                    content=f"Tool execution blocked: input flagged as potentially unsafe (risk: {assessment.risk_score:.2f}).",
                    success=False,
                    metadata={"blocked_reason": "injection_detection"},
                )
        except Exception as e:
            logger.warning("injection_check_skipped", error=str(e))

        # Execute the tool
        logger.info("tool_execution_started", tool=tool_name, workflow_id=str(context.workflow_id))
        try:
            result = await tool.execute(arguments, context)
        except Exception as e:
            logger.error("tool_execution_error", tool=tool_name, error=str(e))
            return ToolResult(content=f"Tool execution failed: {e}", success=False)

        # Post-execution safety: filter content for PII
        if result.success and result.content:
            try:
                from app.safety.content_filter import ContentFilter

                cf = ContentFilter()
                analysis = await cf.analyze(result.content)
                if analysis.pii_detected:
                    result.content = await cf.redact_pii(result.content)
                    result.metadata["pii_redacted"] = True
            except Exception as e:
                logger.warning("content_filter_skipped", error=str(e))

        # Audit log
        try:
            from app.models.base import ActorType, AuditEventType
            from app.services.audit_service import AuditService

            audit = AuditService(session=self.session, redis=self.redis)
            await audit.log(
                event_type=AuditEventType.TOOL_EXECUTED if result.success else AuditEventType.TOOL_BLOCKED,
                actor_type=ActorType.AGENT,
                actor_id=str(context.agent_id),
                workflow_id=context.workflow_id,
                input_summary=f"tool={tool_name} args={str(arguments)[:200]}",
                output_summary=result.content[:200] if result.content else None,
                metadata={"tool": tool_name, "success": result.success},
            )
        except Exception as e:
            logger.warning("tool_audit_log_failed", error=str(e))

        result.cost_credits = tool.estimated_cost_credits
        logger.info(
            "tool_execution_completed",
            tool=tool_name,
            success=result.success,
            cost=tool.estimated_cost_credits,
        )
        return result
```

- [ ] **Step 3: Run tests — verify pass**

Run: `cd backend && pytest tests/unit/test_tool_executor.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add app/tools/executor.py tests/unit/test_tool_executor.py
git commit -m "feat(tools): add ToolExecutor with safety, audit, and cost tracking"
```

---

### Task 8: Scheduler Agentic Loop Integration

**Files:**
- Modify: `backend/app/orchestration/scheduler.py`
- Modify: `backend/app/dependencies.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Modify `_llm_call()` for agentic tool loop**

In `backend/app/orchestration/scheduler.py`, replace the `_llm_call` inner function (lines ~286-310) with the agentic loop version. The key changes:

1. Import tool registry and executor at the top of the method
2. Look up tools for the agent's capabilities
3. Pass `tools=` to `model_router.generate()`
4. When `response.tool_calls` is not None, execute tools and loop back
5. Track all tool calls in task output

The modified `_llm_call` function should be:

```python
async def _llm_call(node: DAGNode) -> tuple[DAGNode, LLMResponse | Exception]:
    task_rec, agent, context = node_contexts[node.node_id]
    system_prompt = AGENT_PROMPTS.get(node.capability, DEFAULT_AGENT_PROMPT)
    user_prompt = self._build_agent_prompt(node, context)

    # Tiered model selection (existing logic)
    if node.capability in HAIKU_CAPABILITIES and node.estimated_complexity != "high":
        model = "claude-haiku-4-5-20251001"
        max_tok = 1000
    elif node.capability in SONNET_CAPABILITIES or node.estimated_complexity == "high":
        model = "claude-sonnet-4-20250514"
        max_tok = 2000
    else:
        model = "claude-sonnet-4-20250514"
        max_tok = 1200

    # Semantic cache check (existing)
    cached_result = await self._check_semantic_cache(node, user_prompt)
    if cached_result:
        logger.info("cache_hit", capability=node.capability, node_id=node.node_id)
        return node, cached_result

    # Resolve tools for this agent's capabilities
    from app.tools.registry import get_tool_registry
    from app.tools.executor import ToolExecutor

    registry = get_tool_registry()
    agent_caps = agent.capabilities if isinstance(agent.capabilities, list) else []
    available_tools = registry.get_tools_for_capabilities(agent_caps)
    tool_schemas = [t.to_schema() for t in available_tools] if available_tools else None

    settings = get_settings()
    max_iterations = settings.tool_max_iterations
    all_tool_calls = []
    current_prompt = user_prompt
    total_cost = Decimal("0")

    for iteration in range(max_iterations):
        try:
            resp = await self.model_router.generate(
                prompt=current_prompt,
                system=system_prompt,
                model=model,
                max_tokens=max_tok,
                temperature=0.4,
                tools=tool_schemas,
            )
            total_cost += resp.cost
        except Exception as e:
            return node, e

        # No tool calls — final response
        if not resp.tool_calls:
            resp.cost = total_cost
            if all_tool_calls:
                resp.metadata["tool_calls"] = all_tool_calls
                resp.metadata["tool_iterations"] = iteration + 1
            await self._store_in_cache(node, user_prompt, resp)
            return node, resp

        # Execute tool calls
        tool_executor = ToolExecutor(session=self.session, redis=self.redis)
        tool_context_obj = ToolContext(
            workflow_id=workflow.workflow_id,
            agent_id=agent.agent_id,
            workspace_path=f"{settings.tool_workspace_base}/{workflow.workflow_id}",
        )
        tool_results_text = []
        for call in resp.tool_calls:
            result = await tool_executor.execute_tool(call.name, call.arguments, tool_context_obj)
            all_tool_calls.append({
                "tool": call.name,
                "arguments": call.arguments,
                "result": result.content[:500],
                "success": result.success,
            })
            tool_results_text.append(
                f"## Tool Result: {call.name}\n"
                f"Success: {result.success}\n"
                f"{result.content}\n"
            )

        # Append tool results to prompt for next iteration
        current_prompt = (
            f"{user_prompt}\n\n"
            f"## Tool Execution Results (iteration {iteration + 1})\n\n"
            + "\n".join(tool_results_text)
            + "\n\nContinue your analysis using these tool results. "
            "If you need more information, call another tool. "
            "Otherwise, provide your final response."
        )

    # Max iterations reached
    resp.cost = total_cost
    resp.metadata["tool_calls"] = all_tool_calls
    resp.metadata["tool_iterations"] = max_iterations
    resp.metadata["max_iterations_reached"] = True
    return node, resp
```

Note: Add `from app.tools.base import ToolContext` to the imports at the top of the file.

- [ ] **Step 2: Add get_tool_executor to dependencies**

Add to `backend/app/dependencies.py`:

```python
from app.tools.executor import ToolExecutor

async def get_tool_executor(
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> ToolExecutor:
    return ToolExecutor(session=session, redis=redis)
```

- [ ] **Step 3: Init tool registry in lifespan**

Add to `backend/app/main.py` lifespan, after injection bank pre-warm:

```python
    # Initialize tool registry
    from app.tools.registry import get_tool_registry
    tool_registry = get_tool_registry()
    logger.info("tool_registry_initialized", tools=len(tool_registry.list_tools()))
```

- [ ] **Step 4: Verify imports work**

Run: `cd backend && python -c "from app.orchestration.scheduler import WorkflowScheduler; from app.tools.registry import get_tool_registry; r = get_tool_registry(); print(f'{len(r.list_tools())} tools registered'); print('OK')"`
Expected: `7 tools registered\nOK`

- [ ] **Step 5: Commit**

```bash
git add app/orchestration/scheduler.py app/dependencies.py app/main.py
git commit -m "feat(tools): integrate agentic tool loop into scheduler with max 5 iterations"
```

---

### Task 9: API Endpoints

**Files:**
- Create: `backend/app/api/tools.py`
- Modify: `backend/app/api/router.py`

- [ ] **Step 1: Create tools API router**

Create `backend/app/api/tools.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.tools.registry import get_tool_registry

router = APIRouter()


@router.get("")
async def list_tools() -> list[dict[str, Any]]:
    """List all available tools with their schemas."""
    registry = get_tool_registry()
    return [
        {
            "name": t.name,
            "description": t.description,
            "execution_mode": t.execution_mode.value,
            "required_capabilities": sorted(t.required_capabilities),
            "estimated_cost_credits": t.estimated_cost_credits,
            "parameters": t.parameters_schema,
        }
        for t in registry.list_tools()
    ]


@router.get("/{tool_name}")
async def get_tool(tool_name: str) -> dict[str, Any]:
    """Get detailed information about a specific tool."""
    registry = get_tool_registry()
    tool = registry.get(tool_name)
    if tool is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    return {
        "name": tool.name,
        "description": tool.description,
        "execution_mode": tool.execution_mode.value,
        "required_capabilities": sorted(tool.required_capabilities),
        "estimated_cost_credits": tool.estimated_cost_credits,
        "parameters": tool.parameters_schema,
        "schema": tool.to_schema(),
    }
```

- [ ] **Step 2: Mount in router**

Add to `backend/app/api/router.py`:

```python
from app.api.tools import router as tools_router

api_router.include_router(tools_router, prefix="/tools", tags=["Tools"])
```

- [ ] **Step 3: Update .env.example**

Add to `.env.example` after `OPENAI_API_KEY`:

```env
# Tool System
BRAVE_SEARCH_API_KEY=
TOOL_MAX_ITERATIONS=5
TOOL_CODE_TIMEOUT_SECONDS=30
TOOL_CODE_MEMORY_MB=256
TOOL_WORKSPACE_BASE=/tmp/massclaw/workspaces
TOOL_DOCKER_IMAGE=python:3.12-slim
```

- [ ] **Step 4: Verify endpoint works**

Run: `cd backend && python -c "from app.api.tools import router; print(f'{len(router.routes)} routes'); print('OK')"`
Expected: `2 routes\nOK`

- [ ] **Step 5: Lint, format, commit**

```bash
cd backend && ruff check app/tools/ app/api/tools.py && ruff format app/tools/ app/api/tools.py
git add app/api/tools.py app/api/router.py .env.example
git commit -m "feat(tools): add /api/v1/tools endpoints and update env config"
```

---

### Task 10: Run Full Test Suite & Final Commit

**Files:** All test files

- [ ] **Step 1: Run all tests**

```bash
cd backend && pytest tests/unit/ tests/integration/ -v --tb=short
```
Expected: ALL PASS

- [ ] **Step 2: Run lint + format**

```bash
cd backend && ruff check app/ && ruff format --check app/
```
Expected: `All checks passed!` + `X files already formatted`

- [ ] **Step 3: Verify tool registry**

```bash
cd backend && python -c "
from app.tools.registry import get_tool_registry
r = get_tool_registry()
for t in r.list_tools():
    print(f'  {t.name:15} mode={t.execution_mode.value:12} caps={sorted(t.required_capabilities)[:3]}...')
print(f'Total: {len(r.list_tools())} tools')
"
```
Expected: 7 tools listed with correct modes and capabilities

- [ ] **Step 4: Final commit and push**

```bash
git add -A
git commit -m "feat(tools): MassClaw Tool Use System — complete implementation

7 built-in tools: web_search, web_scrape, code_execute, file_read, file_write, file_list, api_call
Pluggable ToolProvider interface for third-party tools
Agentic execution loop: LLM → tool_calls → execute → feed results → repeat (max 5)
Capability-based permissions: agents only access tools matching their capabilities
Safety: injection detection on tool args, PII redaction on tool output, SSRF protection
Docker sandbox for code execution with timeout and memory limits
Audit logging for every tool execution
Cost tracking through wallet system"

git push origin main
```

---

## Verification Checklist

After all tasks complete:

1. `GET /api/v1/tools` returns 7 tools with schemas
2. `GET /api/v1/tools/web_search` returns tool details
3. Submit a workflow with a research task → verify agent gets `web_search` tool
4. Submit a workflow with code task → verify Docker sandbox executes
5. `ruff check app/` → zero violations
6. `pytest tests/unit/ tests/integration/ -v` → all pass
7. `git log --oneline -10` → clean commit history
