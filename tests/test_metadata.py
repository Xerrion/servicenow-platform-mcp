"""Tests for metadata tools (meta_list_artifacts, meta_get_artifact, meta_find_references, meta_what_writes)."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from toon_format import decode as toon_decode

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.state import QueryTokenStore

BASE_URL = "https://test.service-now.com"

# Artifact type → ServiceNow table mapping (must match implementation)
ARTIFACT_TABLES = {
    "business_rule": "sys_script",
    "script_include": "sys_script_include",
    "ui_policy": "sys_ui_policy",
    "ui_action": "sys_ui_action",
    "client_script": "sys_script_client",
    "scheduled_job": "sysauto_script",
    "fix_script": "sys_script_fix",
}

# Tables that contain script bodies (for meta_find_references)
SCRIPT_TABLES = [
    "sys_script",
    "sys_script_include",
    "sys_script_client",
    "sys_ui_action",
    "sysauto_script",
    "sys_script_fix",
]


@pytest.fixture
def auth_provider(settings):
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings, auth_provider):
    """Helper: register metadata tools on a fresh MCP server and return tool map + query store."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.metadata import register_tools

    mcp = FastMCP("test")
    query_store = QueryTokenStore()
    mcp._sn_query_store = query_store  # type: ignore[attr-defined]
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}, query_store


class TestMetaListArtifacts:
    """Tests for the meta_list_artifacts tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_lists_artifacts_by_type(self, settings, auth_provider):
        """Lists artifacts filtered by type (e.g., business_rule)."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "br1",
                            "name": "Set Priority",
                            "collection": "incident",
                            "active": "true",
                        },
                        {
                            "sys_id": "br2",
                            "name": "Auto Close",
                            "collection": "incident",
                            "active": "false",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["meta_list_artifacts"](artifact_type="business_rule")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert len(result["data"]["artifacts"]) == 2
        assert result["data"]["artifacts"][0]["name"] == "Set Priority"
        assert result["data"]["artifact_type"] == "business_rule"

    @pytest.mark.asyncio
    @respx.mock
    async def test_lists_artifacts_with_query_filter(self, settings, auth_provider):
        """Filters artifacts by a user-provided query string."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "br1",
                            "name": "Set Priority",
                            "collection": "incident",
                            "active": "true",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools, query_store = _register_and_get_tools(settings, auth_provider)
        token = query_store.create({"query": "collection=incident^active=true"})
        raw = await tools["meta_list_artifacts"](artifact_type="business_rule", query_token=token)
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert len(result["data"]["artifacts"]) == 1

    @pytest.mark.asyncio
    async def test_unknown_type_returns_error(self, settings, auth_provider):
        """Unknown artifact type returns an error."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["meta_list_artifacts"](artifact_type="nonexistent_type")
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert "unknown" in result["error"].lower() or "type" in result["error"].lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_includes_correlation_id(self, settings, auth_provider):
        """Response always contains a correlation_id."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["meta_list_artifacts"](artifact_type="business_rule")
        result = toon_decode(raw)

        assert "correlation_id" in result
        assert len(result["correlation_id"]) > 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_meta_list_artifacts_limit_capped(self, settings, auth_provider):
        """Limit exceeding max_row_limit is capped via enforce_query_safety."""
        route = respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        # Pass limit=9999, which should be capped to settings.max_row_limit (default 100)
        raw = await tools["meta_list_artifacts"](artifact_type="business_rule", limit=9999)
        result = toon_decode(raw)

        assert result["status"] == "success"
        # Verify the request used the capped limit, not the original 9999
        request = route.calls[0].request
        url_str = str(request.url)
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(url_str)
        qs = parse_qs(parsed.query)
        assert qs["sysparm_limit"] == [str(settings.max_row_limit)]


class TestMetaGetArtifact:
    """Tests for the meta_get_artifact tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_full_artifact(self, settings, auth_provider):
        """Returns full artifact details including script body."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br1",
                        "name": "Set Priority",
                        "collection": "incident",
                        "script": "current.priority = 1;",
                        "active": "true",
                        "when": "before",
                    }
                },
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["meta_get_artifact"](artifact_type="business_rule", sys_id="br1")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == "br1"
        assert result["data"]["script"] == "current.priority = 1;"

    @pytest.mark.asyncio
    @respx.mock
    async def test_not_found_returns_error(self, settings, auth_provider):
        """404 from ServiceNow produces an error response."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/missing").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["meta_get_artifact"](artifact_type="business_rule", sys_id="missing")
        result = toon_decode(raw)

        assert result["status"] == "error"


class TestMetaFindReferences:
    """Tests for the meta_find_references tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_uses_code_search_api_when_available(self, settings, auth_provider):
        """Prefers Code Search API and returns results from it."""
        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "search_results": [
                            {
                                "className": "sys_script",
                                "name": "Set Priority",
                                "sys_id": "br1",
                                "field_name": "script",
                                "matches": [{"context": "var gr = new GlideRecord()"}],
                            }
                        ]
                    }
                },
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["meta_find_references"](target="GlideRecord")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert len(result["data"]["matches"]) >= 1
        assert result["data"]["search_method"] == "code_search_api"

    @pytest.mark.asyncio
    @respx.mock
    async def test_falls_back_to_table_search(self, settings, auth_provider):
        """Falls back to per-table scriptCONTAINS when Code Search API fails."""
        # Code Search API fails
        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        # Fallback: per-table search
        for table in SCRIPT_TABLES:
            if table == "sys_script":
                respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "result": [
                                {
                                    "sys_id": "br1",
                                    "name": "Set Priority",
                                    "sys_class_name": "sys_script",
                                }
                            ]
                        },
                        headers={"X-Total-Count": "1"},
                    )
                )
            else:
                respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                    return_value=httpx.Response(
                        200,
                        json={"result": []},
                        headers={"X-Total-Count": "0"},
                    )
                )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["meta_find_references"](target="GlideRecord")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert len(result["data"]["matches"]) >= 1
        assert result["data"]["search_method"] == "table_scan_fallback"
        tables_found = [m["table"] for m in result["data"]["matches"]]
        assert "sys_script" in tables_found

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_references_found(self, settings, auth_provider):
        """Returns empty matches when target string is not found anywhere."""
        # Code Search returns empty
        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"search_results": []}},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["meta_find_references"](target="NonExistentAPI12345")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["matches"] == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_meta_find_references_limit_capped(self, settings, auth_provider):
        """Limit exceeding max_row_limit is capped in fallback per-table queries."""
        # Code Search API fails → triggers fallback
        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        routes: dict[str, respx.Route] = {}
        for table in SCRIPT_TABLES:
            routes[table] = respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(
                    200,
                    json={"result": []},
                    headers={"X-Total-Count": "0"},
                )
            )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        # Pass limit=9999, which should be capped to settings.max_row_limit (default 100)
        raw = await tools["meta_find_references"](target="SomeTarget", limit=9999)
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["search_method"] == "table_scan_fallback"

        # Verify every fallback table query used the capped limit
        from urllib.parse import parse_qs, urlparse

        for table, route in routes.items():
            assert route.called, f"Expected query to {table}"
            request = route.calls[0].request
            parsed = urlparse(str(request.url))
            qs = parse_qs(parsed.query)
            assert qs["sysparm_limit"] == [str(settings.max_row_limit)], f"Table '{table}' should have capped limit"

    @pytest.mark.asyncio
    @respx.mock
    async def test_caret_in_target_single_sanitized(self, settings, auth_provider):
        """Target with carets is single-sanitized (^ → ^^) in fallback CONTAINS queries."""
        # Code Search API fails → triggers fallback
        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        routes: dict[str, respx.Route] = {}
        for table in SCRIPT_TABLES:
            routes[table] = respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(
                    200,
                    json={"result": []},
                    headers={"X-Total-Count": "0"},
                )
            )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["meta_find_references"](target="foo^bar")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["search_method"] == "table_scan_fallback"

        # Verify every fallback query uses single-sanitized value (^^ not ^^^^)
        for table, route in routes.items():
            assert route.called, f"Expected query to {table}"
            request = route.calls[0].request
            parsed = urlparse(str(request.url))
            qs = parse_qs(parsed.query)
            query_str = qs["sysparm_query"][0]
            assert "foo^^bar" in query_str, f"Table '{table}' should have single-sanitized caret"
            assert "foo^^^^bar" not in query_str, f"Table '{table}' should not have double-sanitized caret"


class TestMetaWhatWrites:
    """Tests for the meta_what_writes tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_finds_business_rules_writing_to_table(self, settings, auth_provider):
        """Finds business rules that write to the specified table."""
        # Mock: query sys_script for BRs on the target table
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "br1",
                            "name": "Set Priority",
                            "collection": "incident",
                            "when": "before",
                            "script": "current.priority = 1;",
                            "active": "true",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["meta_what_writes"](table="incident")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert len(result["data"]["writers"]) >= 1
        assert result["data"]["writers"][0]["name"] == "Set Priority"
        assert result["data"]["table"] == "incident"

    @pytest.mark.asyncio
    @respx.mock
    async def test_filters_by_field(self, settings, auth_provider):
        """When field is specified, filters business rules whose script references the field."""
        # Return 2 BRs, but only one references 'priority' in its script
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "br1",
                            "name": "Set Priority",
                            "collection": "incident",
                            "when": "before",
                            "script": "current.priority = 1;",
                            "active": "true",
                        },
                        {
                            "sys_id": "br2",
                            "name": "Log State",
                            "collection": "incident",
                            "when": "after",
                            "script": "gs.log(current.state);",
                            "active": "true",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["meta_what_writes"](table="incident", field="priority")
        result = toon_decode(raw)

        assert result["status"] == "success"
        # Only the BR that references 'priority' should appear
        writers = result["data"]["writers"]
        assert len(writers) == 1
        assert writers[0]["name"] == "Set Priority"

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_writers_found(self, settings, auth_provider):
        """Returns empty writers when no BRs write to the table."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["meta_what_writes"](table="cmdb_ci")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["writers"] == []
