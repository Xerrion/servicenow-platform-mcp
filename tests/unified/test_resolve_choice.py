"""Tests for the unified ``resolve_choice`` tool (Phase 3a)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import DENIED_TABLES
from tests.helpers import decode_response, get_tool_functions


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """BasicAuthProvider for the unified-tool test scope."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> dict[str, Any]:
    """Register the unified ``resolve_choice`` tool on a fresh MCP and return callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.unified.resolve_choice import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider, choices=choices)
    return get_tool_functions(mcp)


def _make_choices(settings: Settings, auth_provider: BasicAuthProvider) -> ChoiceRegistry:
    """Return a ``ChoiceRegistry`` instance pre-marked as fetched (no real I/O)."""
    choices = ChoiceRegistry(settings, auth_provider)
    choices._fetched = True
    return choices


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_resolves_label_to_value(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A known label is resolved to its underlying value via ChoiceRegistry."""
    choices = _make_choices(settings, auth_provider)
    choices.resolve = AsyncMock(return_value="1")  # type: ignore[method-assign]

    tools = _register_and_get_tools(settings, auth_provider, choices=choices)
    raw = await tools["resolve_choice"](table="incident", field="state", label="open")
    result = decode_response(raw)

    assert result["status"] == "success"
    assert result["data"] == {
        "table": "incident",
        "field": "state",
        "label": "open",
        "value": "1",
    }
    choices.resolve.assert_awaited_once_with("incident", "state", "open")


@pytest.mark.asyncio()
async def test_empty_label_returns_full_mapping(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """An empty label returns the full {label: value} mapping for the field."""
    choices = _make_choices(settings, auth_provider)
    mapping = {"open": "1", "in_progress": "2", "closed": "7"}
    choices.get_choices = AsyncMock(return_value=mapping)  # type: ignore[method-assign]

    tools = _register_and_get_tools(settings, auth_provider, choices=choices)
    raw = await tools["resolve_choice"](table="incident", field="state")
    result = decode_response(raw)

    assert result["status"] == "success"
    assert result["data"]["table"] == "incident"
    assert result["data"]["field"] == "state"
    assert result["data"]["choices"] == mapping
    choices.get_choices.assert_awaited_once_with("incident", "state")


# ---------------------------------------------------------------------------
# warning behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_passthrough_emits_warning(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A non-numeric label that resolves to itself triggers a passthrough warning."""
    choices = _make_choices(settings, auth_provider)
    choices.resolve = AsyncMock(side_effect=lambda _t, _f, label: label)  # type: ignore[method-assign]

    tools = _register_and_get_tools(settings, auth_provider, choices=choices)
    raw = await tools["resolve_choice"](table="incident", field="state", label="mystery")
    result = decode_response(raw)

    assert result["status"] == "success"
    assert result["data"]["value"] == "mystery"
    warnings = result.get("warnings") or []
    assert any("mystery" in w and "resolve_choice" in w for w in warnings)


# ---------------------------------------------------------------------------
# policy / defensive guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_denied_table_returns_error(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A denied table is rejected by the table-access policy gate."""
    choices = _make_choices(settings, auth_provider)
    denied = next(iter(DENIED_TABLES))

    tools = _register_and_get_tools(settings, auth_provider, choices=choices)
    raw = await tools["resolve_choice"](table=denied, field="state", label="open")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "denied" in result["error"]["message"].lower()


@pytest.mark.asyncio()
async def test_invalid_field_identifier_returns_error(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A field name that isn't a valid identifier is rejected before any registry call."""
    choices = _make_choices(settings, auth_provider)

    tools = _register_and_get_tools(settings, auth_provider, choices=choices)
    raw = await tools["resolve_choice"](table="incident", field="state^OR1=1", label="open")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "Invalid identifier" in result["error"]["message"]


@pytest.mark.asyncio()
async def test_no_choices_registry_returns_error(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """When ``choices=None``, the tool returns a defensive 'not configured' error."""
    tools = _register_and_get_tools(settings, auth_provider, choices=None)
    raw = await tools["resolve_choice"](table="incident", field="state", label="open")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "ChoiceRegistry" in result["error"]["message"]
