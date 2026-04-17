"""Tests for :class:`ToolRegistry.get_tools_for_agent`.

The regression this guards against: seeded agents had a populated
``supported_tools`` dict in the DB but the scheduler filtered tools purely by
``agent.capabilities``. Every code_execute-requiring workflow came back with
fabricated output because the agent's LLM never saw the tool schema.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.tools.registry import ToolRegistry


def _fresh_registry() -> ToolRegistry:
    return ToolRegistry()


def test_supported_tools_dict_is_authoritative_when_set() -> None:
    registry = _fresh_registry()
    agent = SimpleNamespace(
        supported_tools={"code_execute": True, "file_read": True, "web_search": False},
        capabilities=["unrelated-capability"],
    )
    names = {t.name for t in registry.get_tools_for_agent(agent)}
    assert names == {"code_execute", "file_read"}


def test_supported_tools_list_form_also_honored() -> None:
    registry = _fresh_registry()
    agent = SimpleNamespace(
        supported_tools=["code_execute", "api_call"],
        capabilities=[],
    )
    names = {t.name for t in registry.get_tools_for_agent(agent)}
    assert names == {"code_execute", "api_call"}


def test_falls_back_to_capabilities_when_supported_tools_empty() -> None:
    registry = _fresh_registry()
    agent = SimpleNamespace(
        supported_tools={},  # empty dict → fall back
        capabilities=["code-execution"],
    )
    names = {t.name for t in registry.get_tools_for_agent(agent)}
    # code_execute requires "code-execution"; file_read/list/write and api_call
    # also opt into "code-execution" per their declared required_capabilities.
    assert "code_execute" in names


def test_falls_back_when_supported_tools_missing_attribute() -> None:
    registry = _fresh_registry()
    agent = SimpleNamespace(capabilities=["research"])
    names = {t.name for t in registry.get_tools_for_agent(agent)}
    # research unlocks web_search + web_scrape + file_read + file_list.
    assert "web_search" in names
    assert "code_execute" not in names  # code-execution capability absent


def test_empty_everything_yields_no_tools() -> None:
    registry = _fresh_registry()
    agent = SimpleNamespace(supported_tools=None, capabilities=[])
    assert registry.get_tools_for_agent(agent) == []


def test_supported_tools_names_unknown_to_registry_are_ignored() -> None:
    registry = _fresh_registry()
    agent = SimpleNamespace(
        supported_tools={"code_execute": True, "not_a_real_tool": True},
        capabilities=[],
    )
    names = {t.name for t in registry.get_tools_for_agent(agent)}
    assert names == {"code_execute"}


def test_capability_fallback_respects_capability_semantics() -> None:
    """`get_tools_for_capabilities` still works directly for legacy callers."""
    registry = _fresh_registry()
    tools = registry.get_tools_for_capabilities(["research"])
    names = {t.name for t in tools}
    assert "web_search" in names
    assert "code_execute" not in names
