"""Unit tests for MassClaw built-in tool providers (Tasks 2-5)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools.base import ExecutionMode, ToolContext, ToolResult


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """Return a fresh temporary workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture()
def tool_context(workspace: Path) -> ToolContext:
    """Return a ToolContext wired to the temporary workspace."""
    return ToolContext(
        workflow_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        workspace_path=str(workspace),
        timeout_seconds=30,
        max_iterations=5,
    )


# ===========================================================================
# TASK 2 — File I/O
# ===========================================================================


class TestFileWriteTool:
    @pytest.fixture(autouse=True)
    def _tool(self):
        from app.tools.file_io import FileWriteTool

        self.tool = FileWriteTool()

    async def test_write_file(self, tool_context: ToolContext, workspace: Path):
        result: ToolResult = await self.tool.execute(
            {"path": "hello.txt", "content": "Hello, MassClaw!"},
            tool_context,
        )
        assert result.success is True
        assert (workspace / "hello.txt").exists()
        assert (workspace / "hello.txt").read_text() == "Hello, MassClaw!"

    async def test_reject_path_traversal(self, tool_context: ToolContext):
        result: ToolResult = await self.tool.execute(
            {"path": "../../../etc/passwd", "content": "bad"},
            tool_context,
        )
        assert result.success is False
        assert "traversal" in result.content.lower() or "escapes" in result.content.lower()

    async def test_write_creates_parent_dirs(self, tool_context: ToolContext, workspace: Path):
        result: ToolResult = await self.tool.execute(
            {"path": "subdir/nested/file.txt", "content": "nested content"},
            tool_context,
        )
        assert result.success is True
        assert (workspace / "subdir" / "nested" / "file.txt").read_text() == "nested content"

    async def test_write_missing_path(self, tool_context: ToolContext):
        result: ToolResult = await self.tool.execute({"path": "", "content": "x"}, tool_context)
        assert result.success is False


class TestFileReadTool:
    @pytest.fixture(autouse=True)
    def _tool(self):
        from app.tools.file_io import FileReadTool

        self.tool = FileReadTool()

    async def test_read_existing_file(self, tool_context: ToolContext, workspace: Path):
        (workspace / "data.txt").write_text("sample data")
        result: ToolResult = await self.tool.execute({"path": "data.txt"}, tool_context)
        assert result.success is True
        assert result.content == "sample data"

    async def test_read_nonexistent(self, tool_context: ToolContext):
        result: ToolResult = await self.tool.execute({"path": "ghost.txt"}, tool_context)
        assert result.success is False
        assert "not exist" in result.content.lower() or "does not exist" in result.content.lower()

    async def test_reject_path_traversal(self, tool_context: ToolContext):
        result: ToolResult = await self.tool.execute(
            {"path": "../../../etc/passwd"},
            tool_context,
        )
        assert result.success is False
        assert "traversal" in result.content.lower() or "escapes" in result.content.lower()

    async def test_read_missing_path_arg(self, tool_context: ToolContext):
        result: ToolResult = await self.tool.execute({}, tool_context)
        assert result.success is False


class TestFileListTool:
    @pytest.fixture(autouse=True)
    def _tool(self):
        from app.tools.file_io import FileListTool

        self.tool = FileListTool()

    async def test_list_empty_workspace(self, tool_context: ToolContext):
        result: ToolResult = await self.tool.execute({}, tool_context)
        assert result.success is True
        assert "empty" in result.content.lower()
        assert result.metadata["count"] == 0

    async def test_list_with_files(self, tool_context: ToolContext, workspace: Path):
        (workspace / "alpha.txt").write_text("a")
        (workspace / "beta.txt").write_text("b")
        result: ToolResult = await self.tool.execute({}, tool_context)
        assert result.success is True
        assert "alpha.txt" in result.content
        assert "beta.txt" in result.content
        assert result.metadata["count"] == 2

    async def test_list_reject_traversal(self, tool_context: ToolContext):
        result: ToolResult = await self.tool.execute({"path": "../../etc"}, tool_context)
        assert result.success is False


# ===========================================================================
# TASK 3 — Web Search & Scrape
# ===========================================================================


class TestWebSearchTool:
    @pytest.fixture(autouse=True)
    def _tool(self):
        from app.tools.web_search import WebSearchTool

        self.tool = WebSearchTool()

    async def test_search_no_api_key(self, tool_context: ToolContext):
        mock_settings = MagicMock()
        mock_settings.brave_search_api_key = ""
        mock_settings.firecrawl_api_key = ""
        with patch("app.tools.web_search.get_settings", return_value=mock_settings):
            result: ToolResult = await self.tool.execute({"query": "hello world"}, tool_context)
        assert result.success is False
        # Error message must reference at least one of the supported providers
        # so the agent's LLM can reason about what's missing.
        assert "FIRECRAWL_API_KEY" in result.content or "BRAVE_SEARCH_API_KEY" in result.content

    async def test_search_firecrawl_success(self, tool_context: ToolContext):
        """Firecrawl is the primary provider and is preferred when its key is set."""
        mock_settings = MagicMock()
        mock_settings.brave_search_api_key = ""
        mock_settings.firecrawl_api_key = "fc-test-key"

        fake_response_data: dict[str, Any] = {
            "data": {
                "web": [
                    {
                        "title": "MassClaw Project",
                        "url": "https://example.com/massclaw",
                        "description": "Decentralized AI agent infrastructure.",
                    },
                    {
                        "title": "Another Result",
                        "url": "https://example.com/other",
                        "description": "Second search result.",
                    },
                ]
            }
        }

        mock_response = MagicMock()
        mock_response.json.return_value = fake_response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.get = AsyncMock(return_value=mock_response)

        with (
            patch("app.tools.web_search.get_settings", return_value=mock_settings),
            patch("app.tools.web_search.httpx.AsyncClient", return_value=mock_client),
        ):
            result: ToolResult = await self.tool.execute({"query": "MassClaw AI"}, tool_context)

        assert result.success is True
        assert "MassClaw Project" in result.content
        assert "https://example.com/massclaw" in result.content
        assert result.metadata["count"] == 2
        assert result.metadata["provider"] == "firecrawl"

    async def test_search_brave_fallback_success(self, tool_context: ToolContext):
        """When only Brave key is set, legacy Brave path runs."""
        mock_settings = MagicMock()
        mock_settings.brave_search_api_key = "test-key-123"
        mock_settings.firecrawl_api_key = ""

        fake_response_data: dict[str, Any] = {
            "web": {
                "results": [
                    {
                        "title": "MassClaw Project",
                        "url": "https://example.com/massclaw",
                        "description": "Decentralized AI agent infrastructure.",
                    },
                ]
            }
        }

        mock_response = MagicMock()
        mock_response.json.return_value = fake_response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with (
            patch("app.tools.web_search.get_settings", return_value=mock_settings),
            patch("app.tools.web_search.httpx.AsyncClient", return_value=mock_client),
        ):
            result: ToolResult = await self.tool.execute({"query": "MassClaw AI"}, tool_context)

        assert result.success is True
        assert "MassClaw Project" in result.content
        assert result.metadata["provider"] == "brave"

    async def test_search_missing_query(self, tool_context: ToolContext):
        mock_settings = MagicMock()
        mock_settings.brave_search_api_key = "test-key"
        mock_settings.firecrawl_api_key = ""
        with patch("app.tools.web_search.get_settings", return_value=mock_settings):
            result: ToolResult = await self.tool.execute({}, tool_context)
        assert result.success is False


class TestWebScrapeTool:
    @pytest.fixture(autouse=True)
    def _tool(self):
        from app.tools.web_search import WebScrapeTool

        self.tool = WebScrapeTool()

    async def test_scrape_success(self, tool_context: ToolContext):
        fake_html = (
            "<html><head><title>Test</title><style>body{color:red}</style></head>"
            "<body><p>Hello from MassClaw scraper!</p><script>alert(1)</script></body></html>"
        )

        mock_response = MagicMock()
        mock_response.text = fake_html
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.tools.web_search.httpx.AsyncClient", return_value=mock_client):
            result: ToolResult = await self.tool.execute(
                {"url": "https://example.com"},
                tool_context,
            )

        assert result.success is True
        assert "Hello from MassClaw scraper" in result.content
        # Script and style content should be stripped
        assert "<script>" not in result.content
        assert "alert(1)" not in result.content

    async def test_scrape_missing_url(self, tool_context: ToolContext):
        result: ToolResult = await self.tool.execute({}, tool_context)
        assert result.success is False

    async def test_scrape_max_chars_truncation(self, tool_context: ToolContext):
        long_text = "A" * 200
        fake_html = f"<html><body><p>{long_text}</p></body></html>"

        mock_response = MagicMock()
        mock_response.text = fake_html
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "text/html"}

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.tools.web_search.httpx.AsyncClient", return_value=mock_client):
            result: ToolResult = await self.tool.execute(
                {"url": "https://example.com", "max_chars": 100},
                tool_context,
            )

        assert result.success is True
        assert len(result.content) <= 101  # 100 chars + ellipsis
        assert result.metadata["truncated"] is True


# ===========================================================================
# TASK 4 — API Caller
# ===========================================================================


class TestAPICallerTool:
    @pytest.fixture(autouse=True)
    def _tool(self):
        from app.tools.api_caller import APICallerTool

        self.tool = APICallerTool()

    async def test_block_private_ip(self, tool_context: ToolContext):
        result: ToolResult = await self.tool.execute(
            {"url": "http://127.0.0.1/admin"},
            tool_context,
        )
        assert result.success is False
        assert "private" in result.content.lower() or "not permitted" in result.content.lower()
        assert result.metadata.get("blocked") is True

    async def test_block_private_network(self, tool_context: ToolContext):
        result: ToolResult = await self.tool.execute(
            {"url": "http://192.168.1.1/"},
            tool_context,
        )
        assert result.success is False
        assert result.metadata.get("blocked") is True

    async def test_block_localhost(self, tool_context: ToolContext):
        result: ToolResult = await self.tool.execute(
            {"url": "http://localhost/"},
            tool_context,
        )
        assert result.success is False
        assert result.metadata.get("blocked") is True

    async def test_success(self, tool_context: ToolContext):
        fake_body = '{"status": "ok", "data": "hello"}'

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = fake_body

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.request = AsyncMock(return_value=mock_response)

        with (
            patch("app.tools.api_caller._is_private_url", return_value=False),
            patch("app.tools.api_caller.httpx.AsyncClient", return_value=mock_client),
        ):
            result: ToolResult = await self.tool.execute(
                {"url": "https://api.example.com/status", "method": "GET"},
                tool_context,
            )

        assert result.success is True
        assert "200" in result.content
        assert "hello" in result.content
        assert result.metadata["status_code"] == 200

    async def test_invalid_method(self, tool_context: ToolContext):
        with patch("app.tools.api_caller._is_private_url", return_value=False):
            result: ToolResult = await self.tool.execute(
                {"url": "https://api.example.com/", "method": "OPTIONS"},
                tool_context,
            )
        assert result.success is False
        assert "unsupported" in result.content.lower()

    async def test_missing_url(self, tool_context: ToolContext):
        result: ToolResult = await self.tool.execute({}, tool_context)
        assert result.success is False


# ===========================================================================
# TASK 5 — Code Execution
# ===========================================================================


class TestCodeExecutionTool:
    @pytest.fixture(autouse=True)
    def _tool(self):
        from app.tools.code_execution import CodeExecutionTool

        self.tool = CodeExecutionTool()

    def test_schema(self):
        """Verify tool metadata without running Docker."""
        assert self.tool.name == "code_execute"
        assert self.tool.execution_mode == ExecutionMode.SANDBOXED
        assert "code-execution" in self.tool.required_capabilities

    async def test_reject_empty_code(self, tool_context: ToolContext):
        # Provide a stub aiodocker so we reach the empty-code validation branch
        mock_aiodocker = MagicMock()
        with patch.dict("sys.modules", {"aiodocker": mock_aiodocker}):
            result: ToolResult = await self.tool.execute(
                {"language": "python", "code": ""},
                tool_context,
            )
        assert result.success is False
        assert "empty" in result.content.lower() or "required" in result.content.lower()

    async def test_docker_unavailable(self, tool_context: ToolContext):
        """When Docker is not running, return a helpful error."""
        result: ToolResult = await self.tool.execute(
            {"language": "python", "code": "print('hi')"},
            tool_context,
        )
        # Docker may or may not be running — if not, should fail gracefully
        if not result.success:
            assert "docker" in result.content.lower() or "unavailable" in result.content.lower()

    async def test_unsupported_language(self, tool_context: ToolContext):
        # Patch out the aiodocker import check so we reach the language validation
        mock_aiodocker = MagicMock()
        with patch.dict("sys.modules", {"aiodocker": mock_aiodocker}):
            result: ToolResult = await self.tool.execute(
                {"language": "ruby", "code": "puts 'hi'"},
                tool_context,
            )
        assert result.success is False
        assert "unsupported" in result.content.lower()
