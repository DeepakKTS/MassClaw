from __future__ import annotations

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
        assert len(names) == 7

    def test_get_tool_by_name(self):
        registry = ToolRegistry()
        tool = registry.get("web_search")
        assert tool is not None
        assert tool.name == "web_search"

    def test_get_nonexistent_returns_none(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_get_tools_for_research(self):
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

    def test_text_only_capability_gets_no_tools(self):
        registry = ToolRegistry()
        tools = registry.get_tools_for_capabilities(["intake"])
        assert len(tools) == 0

    def test_to_schemas(self):
        registry = ToolRegistry()
        tools = registry.get_tools_for_capabilities(["research"])
        schemas = [t.to_schema() for t in tools]
        assert all("name" in s and "input_schema" in s for s in schemas)
