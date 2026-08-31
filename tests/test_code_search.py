"""Tests for the unified ``code_search`` tool."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from tests.helpers import decode_response, get_registered_tools, get_tool_functions


BASE_URL = "https://test.service-now.com"
SEARCH_URL = f"{BASE_URL}/api/sn_codesearch/code_search/search"
TABLES_URL = f"{BASE_URL}/api/sn_codesearch/code_search/tables"


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """BasicAuthProvider for the code_search tool test scope."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Register the unified ``code_search`` tool on a fresh MCP and return callables."""
    from mcp.server import MCPServer

    from servicenow_mcp.tools.code_search import register_tools

    mcp = MCPServer("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


async def test_schema_exposes_agent_callable_parameters(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """The MCP schema exposes callable inputs and hides injected correlation_id."""
    from mcp.server import MCPServer

    from servicenow_mcp.tools.code_search import register_tools

    mcp = MCPServer("test")
    register_tools(mcp, settings, auth_provider)
    tool = (await get_registered_tools(mcp))["code_search"]

    properties = tool.input_schema.get("properties", {})
    assert "action" in properties
    assert "term" in properties
    assert "table" in properties
    assert "search_group" in properties
    assert "limit" in properties
    assert "correlation_id" not in properties


@pytest.mark.asyncio()
@respx.mock
async def test_search_calls_code_search_api(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Search action calls the ServiceNow Code Search API."""
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "search_results": [
                        {
                            "className": "sys_script_include",
                            "name": "TestUtil",
                            "match": "AbstractAjaxProcessor",
                        }
                    ]
                }
            },
        )
    )

    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["code_search"](term="AbstractAjaxProcessor", table="sys_script_include", limit=5)
    result = decode_response(raw)

    assert result["status"] == "success"
    assert result["data"]["search_results"][0]["name"] == "TestUtil"
    assert route.calls.last is not None
    url = str(route.calls.last.request.url)
    assert "term=AbstractAjaxProcessor" in url
    assert "table=sys_script_include" in url
    assert "limit=5" in url


@pytest.mark.asyncio()
@respx.mock
async def test_search_passes_search_group(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Search action forwards an optional Code Search group."""
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"result": {}}))

    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["code_search"](
        term="current.update",
        search_group="sn_codesearch.Default Search Group",
    )
    result = decode_response(raw)

    assert result["status"] == "success"
    assert route.calls.last is not None
    assert "search_group" in str(route.calls.last.request.url)


@pytest.mark.asyncio()
@respx.mock
async def test_list_tables_calls_tables_api(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """list_tables action calls the Code Search tables endpoint."""
    route = respx.get(TABLES_URL).mock(
        return_value=httpx.Response(
            200,
            json={"result": {"tables": [{"name": "sys_script_include"}, {"name": "sys_script"}]}},
        )
    )

    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["code_search"](action="list_tables")
    result = decode_response(raw)

    assert result["status"] == "success"
    assert result["data"]["tables"][0]["name"] == "sys_script_include"
    assert route.called


@pytest.mark.asyncio()
async def test_describe_returns_action_registry(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """describe action returns the local action registry without platform I/O."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["code_search"](action="describe")
    result = decode_response(raw)

    assert result["status"] == "success"
    assert set(result["data"]["actions"]) == {"search", "list_tables", "describe"}


@pytest.mark.asyncio()
async def test_search_requires_term(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Search action rejects empty terms before making a platform call."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["code_search"]()
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "'term' is required" in result["error"]["message"]


@pytest.mark.asyncio()
async def test_search_rejects_invalid_table(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """The optional table filter must be a safe ServiceNow identifier."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["code_search"](term="foo", table="sys_script^ORactive=true")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "Invalid identifier" in result["error"]["message"]


@pytest.mark.asyncio()
async def test_search_rejects_non_positive_limit(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Search action rejects a non-positive limit before making a platform call."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["code_search"](term="foo", limit=0)
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "limit must be greater than 0" in result["error"]["message"]
