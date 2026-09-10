"""Tests for the unified ``code_search`` tool."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.config import Settings
from tests.helpers import decode_response, get_registered_tools, get_tool_functions


BASE_URL = "https://test.service-now.com"
SEARCH_URL = f"{BASE_URL}/api/sn_codesearch/code_search/search"
TABLES_URL = f"{BASE_URL}/api/sn_codesearch/code_search/tables"


@pytest.fixture()
def auth_provider(settings: Settings) -> OAuthPKCEProvider:
    """OAuthPKCEProvider for the code_search tool test scope."""
    return OAuthPKCEProvider(settings)


def _register_and_get_tools(settings: Settings, auth_provider: OAuthPKCEProvider) -> dict[str, Any]:
    """Register the unified ``code_search`` tool on a fresh MCP and return callables."""
    from mcp.server import MCPServer

    from servicenow_mcp.tools.code_search import register_tools

    mcp = MCPServer("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


class TestCodeSearch:
    """Code Search schema, requests, and input validation."""

    async def test_schema_exposes_agent_callable_parameters(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        """The MCP schema exposes only callable inputs."""
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
        assert properties["extended_matching"]["default"] is False
        assert "correlation_id" not in properties

    @pytest.mark.asyncio()
    @respx.mock
    async def test_search_calls_code_search_api(self, settings: Settings, auth_provider: OAuthPKCEProvider) -> None:
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
        assert route.calls.last.request.url.params["search_group"] == "sn_codesearch.Default Search Group"
        assert route.calls.last.request.url.params["extended_matching"] == "false"

    @pytest.mark.parametrize("extended_matching", [False, True])
    @respx.mock
    async def test_search_context_opt_in_preserves_platform_metadata(
        self, settings: Settings, auth_provider: OAuthPKCEProvider, extended_matching: bool
    ) -> None:
        """Context is opt-in; limit metadata and platform completeness signals survive."""
        settings.max_row_limit = 5
        payload = {
            "search_results": [{"className": "sys_script_include", "name": "TestUtil", "match": "foo"}],
            "warnings": ["Results truncated; narrow the table filter."],
            "has_more": True,
        }
        route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"result": payload}))
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["code_search"](term="foo", limit=100, extended_matching=extended_matching))

        assert result["status"] == "success"
        assert "correlation_id" not in result
        assert result["data"] == payload
        assert "warnings" not in result
        assert result["pagination"] == {"limit": 5}
        assert route.calls.last.request.url.params["limit"] == "5"
        assert route.calls.last.request.url.params["extended_matching"] == str(extended_matching).lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_search_passes_search_group(self, settings: Settings, auth_provider: OAuthPKCEProvider) -> None:
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
    async def test_list_tables_calls_tables_api(self, settings: Settings, auth_provider: OAuthPKCEProvider) -> None:
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
        assert route.calls.last.request.url.params["search_group"] == "sn_codesearch.Default Search Group"

    @pytest.mark.asyncio()
    async def test_describe_returns_action_registry(self, settings: Settings, auth_provider: OAuthPKCEProvider) -> None:
        """describe action returns the local action registry without platform I/O."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["code_search"](action="describe")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert set(result["data"]["actions"]) == {"search", "list_tables", "describe"}

    @pytest.mark.asyncio()
    async def test_search_requires_term(self, settings: Settings, auth_provider: OAuthPKCEProvider) -> None:
        """Search action rejects empty terms before making a platform call."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["code_search"]()
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "'term' is required" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_search_rejects_invalid_table(self, settings: Settings, auth_provider: OAuthPKCEProvider) -> None:
        """The optional table filter must be a safe ServiceNow identifier."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["code_search"](term="foo", table="sys_script^ORactive=true")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "Invalid identifier" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_search_rejects_non_positive_limit(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        """Search action rejects a non-positive limit before making a platform call."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["code_search"](term="foo", limit=0)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "limit must be greater than 0" in result["error"]["message"]
