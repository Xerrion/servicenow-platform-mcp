"""Tests for the unified ``code_search`` tool."""

from __future__ import annotations

from typing import Any

import pytest
import respx
from httpx import Response

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from tests.helpers import decode_response, get_tool_functions


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """BasicAuthProvider for the unified-tool test scope."""
    return BasicAuthProvider(settings)


INSTANCE_URL: str = "https://test.service-now.com"
SEARCH_URL: str = f"{INSTANCE_URL}/api/sn_codesearch/code_search/search"
TABLES_URL: str = f"{INSTANCE_URL}/api/sn_codesearch/code_search/tables"


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Register the unified ``code_search`` tool on a fresh MCP and return callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.code_search import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


# ---------------------------------------------------------------------------
# search action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_search_success(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """A successful code search returns formatted results."""
    mock_response: dict[str, Any] = {
        "result": [
            {"table": "sys_script_include", "name": "MyScript", "match": "function myFunc()", "sys_id": "abc123"},
            {"table": "sys_script_include", "name": "MyScript", "match": "function myFunc()", "sys_id": "def456"},
        ],
    }
    with respx.mock:
        respx.get(SEARCH_URL).mock(return_value=Response(200, json=mock_response))

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["code_search"](action="search", term="myFunc")
        result = decode_response(raw)

    assert result["status"] == "success"
    assert isinstance(result["data"], list)
    assert len(result["data"]) == 2


@pytest.mark.asyncio()
async def test_search_empty_term(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """An empty search term is rejected with an error before any API call."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["code_search"](action="search", term="")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "term is required" in result["error"]["message"].lower()


@pytest.mark.asyncio()
async def test_search_with_table_filter(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """When ``table`` is provided it is passed through to the client."""
    mock_response: dict[str, Any] = {"result": [{"table": "sys_script_include", "name": "MyScript", "match": "fn"}]}
    with respx.mock:
        route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=mock_response))

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["code_search"](action="search", term="fn", table="sys_script_include")
        result = decode_response(raw)

    assert result["status"] == "success"
    # Verify table param was sent
    assert route.calls.last.request.url.params.get("table") == "sys_script_include"


@pytest.mark.asyncio()
async def test_search_clamps_limit_above_max(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """A limit > 500 is clamped to 500."""
    mock_response: dict[str, Any] = {"result": []}
    with respx.mock:
        route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=mock_response))

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["code_search"](action="search", term="fn", limit=9999)
        result = decode_response(raw)

    assert result["status"] == "success"
    assert route.calls.last.request.url.params.get("limit") == "500"


@pytest.mark.asyncio()
async def test_search_clamps_limit_below_min(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """A limit < 1 is clamped to 1."""
    mock_response: dict[str, Any] = {"result": []}
    with respx.mock:
        route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=mock_response))

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["code_search"](action="search", term="fn", limit=0)
        result = decode_response(raw)

    assert result["status"] == "success"
    assert route.calls.last.request.url.params.get("limit") == "1"


@pytest.mark.asyncio()
async def test_search_passes_search_group(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """When ``search_group`` is provided it is passed to the client."""
    mock_response: dict[str, Any] = {"result": []}
    with respx.mock:
        route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=mock_response))

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["code_search"](action="search", term="fn", search_group="my_group")
        result = decode_response(raw)

    assert result["status"] == "success"
    assert route.calls.last.request.url.params.get("search_group") == "my_group"


# ---------------------------------------------------------------------------
# tables action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_tables_action(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """The tables action fetches and returns the list of searchable tables."""
    mock_response: dict[str, Any] = {
        "result": [
            {"name": "sys_script_include", "label": "Script Includes"},
            {"name": "sys_script", "label": "Business Rules"},
        ],
    }
    with respx.mock:
        respx.get(TABLES_URL).mock(return_value=Response(200, json=mock_response))

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["code_search"](action="tables")
        result = decode_response(raw)

    assert result["status"] == "success"
    assert isinstance(result["data"], list)
    assert len(result["data"]) == 2
    assert result["data"][0]["name"] == "sys_script_include"


# ---------------------------------------------------------------------------
# describe action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_describe_action(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """The describe action returns metadata without any API call."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["code_search"](action="describe")
    result = decode_response(raw)

    assert result["status"] == "success"
    assert "actions" in result["data"]
    assert "search" in result["data"]["actions"]
    assert "tables" in result["data"]["actions"]
    assert "describe" in result["data"]["actions"]


# ---------------------------------------------------------------------------
# unknown action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_unknown_action_returns_error(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """An unknown action value is rejected with a clear error message."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["code_search"](action="nonexistent")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "nonexistent" in result["error"]["message"]
