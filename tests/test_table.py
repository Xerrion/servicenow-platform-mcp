"""Tests for table-level tools (table_describe, table_query, table_aggregate, build_query)."""

import json
from typing import Any

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.mcp_state import attach_query_store
from servicenow_mcp.policy import DENIED_TABLES
from servicenow_mcp.state import QueryTokenStore
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(
    settings: Settings, auth_provider: BasicAuthProvider
) -> tuple[dict[str, Any], QueryTokenStore]:
    """Helper: register table tools on a fresh MCP server and return tool map + query store."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.table import register_tools

    mcp = FastMCP("test")
    query_store = QueryTokenStore()
    attach_query_store(mcp, query_store)
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp), query_store


# ── table_describe ───────────────────────────────────────────────────────


class TestTableDescribe:
    """Tests for the table_describe tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_field_metadata(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns structured field metadata for a known table."""
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "element": "number",
                            "internal_type": "string",
                            "max_length": "40",
                            "mandatory": "true",
                            "reference": "",
                            "column_label": "Number",
                            "default_value": "",
                        },
                        {
                            "element": "state",
                            "internal_type": "integer",
                            "max_length": "40",
                            "mandatory": "false",
                            "reference": "",
                            "column_label": "State",
                            "default_value": "1",
                        },
                    ]
                },
            )
        )
        # table_describe also queries sys_db_object and sys_documentation
        respx.get(f"{BASE_URL}/api/now/table/sys_db_object").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "name": "incident",
                            "label": "Incident",
                            "super_class": "",
                            "is_extendable": "true",
                            "number_ref": "",
                            "sys_id": "abc123",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_documentation").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_describe"](table="incident")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["field_count"] == 2
        assert result["data"]["fields"][0]["element"] == "number"
        assert result["data"]["fields"][1]["element"] == "state"

    @pytest.mark.asyncio()
    async def test_denied_table_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Blocked tables return an error response (no HTTP call made)."""
        denied = next(iter(DENIED_TABLES))
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_describe"](table=denied)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_includes_correlation_id(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Response always contains a correlation_id."""
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(200, json={"result": []})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_db_object").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_documentation").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_describe"](table="incident")
        result = decode_response(raw)

        assert "correlation_id" in result
        assert len(result["correlation_id"]) > 0


# ── table_query ──────────────────────────────────────────────────────────


class TestTableQuery:
    """Tests for the table_query tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_matching_records(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns records matching the query with pagination."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {"sys_id": "1", "number": "INC0001"},
                        {"sys_id": "2", "number": "INC0002"},
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools, query_store = _register_and_get_tools(settings, auth_provider)
        token = await query_store.create({"query": "active=true"})
        raw = await tools["table_query"](table="incident", query_token=token)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]) == 2
        assert result["pagination"]["total"] == 2

    @pytest.mark.asyncio()
    @respx.mock
    async def test_limit_capped_with_warning(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """When requested limit exceeds max_row_limit, it is capped and a warning is added."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools, query_store = _register_and_get_tools(settings, auth_provider)
        token = await query_store.create({"query": "active=true"})
        # Default max_row_limit is 100, request 500
        raw = await tools["table_query"](table="incident", query_token=token, limit=500)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["pagination"]["limit"] == 100
        assert isinstance(result, dict)
        assert any("capped" in w.lower() for w in result.get("warnings", []))

    @pytest.mark.asyncio()
    async def test_large_table_without_date_filter_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Large tables require a date filter; omitting it returns an error."""
        # Add syslog to large tables for this test
        settings.large_table_names_csv = "syslog,sys_audit"
        tools, query_store = _register_and_get_tools(settings, auth_provider)
        token = await query_store.create({"query": "level=error"})
        raw = await tools["table_query"](table="syslog", query_token=token)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "date" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_denied_table_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Denied table returns error."""
        denied = next(iter(DENIED_TABLES))
        tools, query_store = _register_and_get_tools(settings, auth_provider)
        token = await query_store.create({"query": "active=true"})
        raw = await tools["table_query"](table=denied, query_token=token)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_display_values_passed_to_client(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """When display_values=True, sysparm_display_value=true is sent to the API."""
        route = respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "1",
                            "number": "INC0001",
                            "priority": "1 - Critical",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools, query_store = _register_and_get_tools(settings, auth_provider)
        token = await query_store.create({"query": "active=true"})
        raw = await tools["table_query"](table="incident", query_token=token, display_values=True)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]) == 1

        # Verify the request included sysparm_display_value=true
        assert route.called
        request = route.calls.last.request
        assert "sysparm_display_value=true" in str(request.url)


# ── table_aggregate ──────────────────────────────────────────────────────


class TestTableAggregate:
    """Tests for the table_aggregate tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_aggregate_stats(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns aggregate statistics for the query."""
        respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"stats": {"count": "42"}}},
            )
        )

        tools, query_store = _register_and_get_tools(settings, auth_provider)
        token = await query_store.create({"query": "active=true"})
        raw = await tools["table_aggregate"](table="incident", query_token=token)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["stats"]["count"] == "42"

    @pytest.mark.asyncio()
    async def test_denied_table_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Denied table returns error."""
        denied = next(iter(DENIED_TABLES))
        tools, query_store = _register_and_get_tools(settings, auth_provider)
        token = await query_store.create({"query": "active=true"})
        raw = await tools["table_aggregate"](table=denied, query_token=token)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()


# ── Error propagation ────────────────────────────────────────────────────


class TestTableErrorPropagation:
    """Verify that ServiceNowMCPError subclasses raised by the client are caught
    by the tool layer and returned inside a format_response error envelope."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_server_error_returns_error_envelope(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """ServerError (500) from client is caught and returned in error envelope."""
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(
                500,
                json={"error": {"message": "Internal server error"}},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_describe"](table="incident")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert (
            "internal server error" in result["error"]["message"].lower()
            or "server error" in result["error"]["message"].lower()
        )
        assert "correlation_id" in result

    @pytest.mark.asyncio()
    @respx.mock
    async def test_query_tool_auth_error_returns_error_envelope(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """AuthError during table_query is caught and returned in error envelope."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                401,
                json={"error": {"message": "Session expired"}},
            )
        )

        tools, query_store = _register_and_get_tools(settings, auth_provider)
        token = await query_store.create({"query": "active=true"})
        raw = await tools["table_query"](table="incident", query_token=token)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert (
            "session expired" in result["error"]["message"].lower()
            or "authentication" in result["error"]["message"].lower()
        )
        assert "correlation_id" in result

    @pytest.mark.asyncio()
    @respx.mock
    async def test_aggregate_tool_server_error_returns_error_envelope(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """ServerError during table_aggregate is caught and returned in error envelope."""
        respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(
                502,
                json={"error": {"message": "Bad gateway"}},
            )
        )

        tools, query_store = _register_and_get_tools(settings, auth_provider)
        token = await query_store.create({"query": "active=true"})
        raw = await tools["table_aggregate"](table="incident", query_token=token)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert result["error"]["message"]  # Error message is non-empty
        assert "correlation_id" in result


# ── Query token validation ───────────────────────────────────────────────


class TestQueryTokenValidation:
    """Tests that query token validation works correctly."""

    @pytest.mark.asyncio()
    async def test_invalid_token_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Passing a non-existent token returns a descriptive error."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_query"](table="incident", query_token="not-a-real-token")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "build_query" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_token_queries_without_filter(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Empty query_token runs query with no filter."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": [{"sys_id": "1"}]},
                headers={"X-Total-Count": "1"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_query"](table="incident")
        result = decode_response(raw)

        assert result["status"] == "success"


# ── build_query ──────────────────────────────────────────────────────────


class TestBuildQuery:
    """Tests for the build_query MCP tool."""

    @pytest.mark.asyncio()
    async def test_simple_equals(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Build a simple equals query."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions='[{"operator": "equals", "field": "active", "value": "true"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "active=true"
        assert "query_token" in result["data"]
        assert isinstance(result["data"]["query_token"], str)
        assert len(result["data"]["query_token"]) > 0

    @pytest.mark.asyncio()
    async def test_with_time_filter(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Build a query with hours_ago time filter."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "hours_ago", "field": "sys_created_on", "value": 24}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_on>=javascript:gs.hoursAgoStart(24)"
        assert "query_token" in result["data"]
        assert isinstance(result["data"]["query_token"], str)
        assert len(result["data"]["query_token"]) > 0

    @pytest.mark.asyncio()
    async def test_multiple_conditions(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Build a query with multiple conditions."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "active", "value": "true"},
                {
                    "operator": "hours_ago",
                    "field": "sys_created_on",
                    "value": 24,
                },
                {"operator": "like", "field": "source", "value": "incident"},
            ]
        )
        raw = await tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == (
            "active=true^sys_created_on>=javascript:gs.hoursAgoStart(24)^sourceLIKEincident"
        )
        assert "query_token" in result["data"]
        assert isinstance(result["data"]["query_token"], str)
        assert len(result["data"]["query_token"]) > 0

    @pytest.mark.asyncio()
    async def test_is_empty_no_value_needed(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Unary operators like is_empty don't need a value."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions='[{"operator": "is_empty", "field": "assigned_to"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "assigned_toISEMPTY"
        assert "query_token" in result["data"]
        assert isinstance(result["data"]["query_token"], str)
        assert len(result["data"]["query_token"]) > 0

    @pytest.mark.asyncio()
    async def test_invalid_operator_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Unknown operator returns an error response."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions='[{"operator": "INVALID", "field": "active", "value": "true"}]')
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "Unknown operator" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_invalid_json_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Malformed JSON returns an error response."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions="not valid json")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "Invalid JSON" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_missing_field_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Missing required 'field' key returns an error."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions='[{"operator": "equals", "value": "true"}]')
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "requires a 'field'" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_missing_value_for_binary_operator_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Binary operators require a value."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions='[{"operator": "equals", "field": "active"}]')
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "requires a 'value'" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_not_array_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Non-array JSON returns an error."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions='{"operator": "equals"}')
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "must be a JSON array" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_empty_array_returns_empty_query(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Empty array returns empty query string."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions="[]")
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == ""
        assert "query_token" in result["data"]

    @pytest.mark.asyncio()
    async def test_days_ago_operator(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test days_ago time filter."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "days_ago", "field": "sys_created_on", "value": 30}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_on>=javascript:gs.daysAgoStart(30)"
        assert "query_token" in result["data"]

    @pytest.mark.asyncio()
    async def test_starts_with_operator(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test starts_with string operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "starts_with", "field": "name", "value": "incident"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "nameSTARTSWITHincident"
        assert "query_token" in result["data"]

    @pytest.mark.asyncio()
    async def test_or_equals_operator(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test or_equals OR condition."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "state", "value": "1"},
                {"operator": "or_equals", "field": "state", "value": "2"},
            ]
        )
        raw = await tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "state=1^ORstate=2"
        assert "query_token" in result["data"]

    @pytest.mark.asyncio()
    async def test_or_starts_with_operator(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test or_starts_with OR condition."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "starts_with", "field": "name", "value": "INC"},
                {
                    "operator": "or_starts_with",
                    "field": "name",
                    "value": "REQ",
                },
            ]
        )
        raw = await tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "nameSTARTSWITHINC^ORnameSTARTSWITHREQ"
        assert "query_token" in result["data"]

    @pytest.mark.asyncio()
    async def test_in_list_operator(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test in_list operator with a list of values."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {
                    "operator": "in_list",
                    "field": "state",
                    "value": ["1", "2", "3"],
                },
            ]
        )
        raw = await tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "stateIN1,2,3"
        assert "query_token" in result["data"]

    @pytest.mark.asyncio()
    async def test_not_in_list_operator(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test not_in_list operator with a list of values."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {
                    "operator": "not_in_list",
                    "field": "priority",
                    "value": ["4", "5"],
                },
            ]
        )
        raw = await tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "priorityNOT IN4,5"
        assert "query_token" in result["data"]

    @pytest.mark.asyncio()
    async def test_in_list_requires_list_value(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """in_list with a non-list value returns an error."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "in_list", "field": "state", "value": "1"},
            ]
        )
        raw = await tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "list of strings" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_order_by_ascending(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test order_by operator (ascending by default)."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "active", "value": "true"},
                {"operator": "order_by", "field": "sys_created_on"},
            ]
        )
        raw = await tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "active=true^ORDERBYsys_created_on"
        assert "query_token" in result["data"]

    @pytest.mark.asyncio()
    async def test_order_by_descending(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test order_by operator with descending=true."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "active", "value": "true"},
                {
                    "operator": "order_by",
                    "field": "sys_created_on",
                    "descending": True,
                },
            ]
        )
        raw = await tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "active=true^ORDERBYDESCsys_created_on"
        assert "query_token" in result["data"]

    @pytest.mark.asyncio()
    async def test_value_injection_prevented(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Value containing ^ is escaped by the builder, preventing injection."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "name", "value": "foo^bar"},
            ]
        )
        raw = await tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        # The ^ in the value should be escaped to ^^
        assert result["data"]["query"] == "name=foo^^bar"
        assert "query_token" in result["data"]

    @pytest.mark.asyncio()
    async def test_hours_ago_missing_value_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Time operator without value key returns error (line 96)."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "hours_ago", "field": "sys_created_on"}]',
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "requires an integer 'value'" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_unexpected_exception_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Unexpected exception in ServiceNowQuery triggers generic handler."""
        from unittest.mock import patch

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        with patch(
            "servicenow_mcp.tools.table.ServiceNowQuery",
            side_effect=RuntimeError("boom"),
        ):
            raw = await tools["build_query"](conditions='[{"operator": "equals", "field": "active", "value": "true"}]')
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "boom" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_query_token_is_resolvable(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """build_query returns a token that resolves back to the built query."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.table import register_tools

        query_store = QueryTokenStore()
        mcp = FastMCP("test")
        attach_query_store(mcp, query_store)
        register_tools(mcp, settings, auth_provider)
        tools = get_tool_functions(mcp)

        raw = await tools["build_query"](conditions='[{"operator": "equals", "field": "active", "value": "true"}]')
        result = decode_response(raw)
        token = result["data"]["query_token"]

        payload = await query_store.get(token)
        assert payload is not None
        assert payload["query"] == "active=true"

    # -- New operator tests (Phase 9) ------------------------------------------

    @pytest.mark.asyncio()
    async def test_ends_with(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test ends_with string operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "ends_with", "field": "email", "value": "@example.com"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "emailENDSWITH@example.com"

    @pytest.mark.asyncio()
    async def test_not_like(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test not_like string operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions='[{"operator": "not_like", "field": "name", "value": "test"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "nameNOT LIKEtest"

    @pytest.mark.asyncio()
    async def test_anything(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test anything unary operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions='[{"operator": "anything", "field": "state"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "stateANYTHING"

    @pytest.mark.asyncio()
    async def test_empty_string(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test empty_string unary operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions='[{"operator": "empty_string", "field": "description"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "descriptionEMPTYSTRING"

    @pytest.mark.asyncio()
    async def test_val_changes(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test val_changes unary operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions='[{"operator": "val_changes", "field": "state"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "stateVALCHANGES"

    @pytest.mark.asyncio()
    async def test_gt_field(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test gt_field comparison operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "gt_field", "field": "sys_updated_on", "other_field": "sys_created_on"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_updated_onGT_FIELDsys_created_on"

    @pytest.mark.asyncio()
    async def test_same_as(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test same_as field comparison operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "same_as", "field": "assigned_to", "other_field": "opened_by"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "assigned_toSAMEASopened_by"

    @pytest.mark.asyncio()
    async def test_field_operator_with_value_fallback(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Field operators should accept 'value' as fallback for 'other_field'."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "lt_field", "field": "priority", "value": "impact"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "priorityLT_FIELDimpact"

    @pytest.mark.asyncio()
    async def test_field_operator_missing_other_field(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Field operators without other_field or value return error."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions='[{"operator": "gt_field", "field": "priority"}]')
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_between(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test between range operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "between", "field": "sys_created_on", "start": "2026-01-01", "end": "2026-12-31"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_onBETWEEN2026-01-01@2026-12-31"

    @pytest.mark.asyncio()
    async def test_between_missing_end(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """between without end value returns error."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "between", "field": "sys_created_on", "start": "2026-01-01"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_datepart(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test datepart operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "datepart", "field": "sys_created_on", "part": "dayofweek", "dp_operator": "=", "dp_value": "1"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_onDATEPARTdayofweek@=@1"

    @pytest.mark.asyncio()
    async def test_datepart_missing_part(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """datepart without required params returns error."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions='[{"operator": "datepart", "field": "sys_created_on"}]')
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_on(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test on date operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "on", "field": "sys_created_on", "value": "2026-01-15"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_onON2026-01-15"

    @pytest.mark.asyncio()
    async def test_relative_gt(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test relative_gt date operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "relative_gt", "field": "sys_created_on", "value": "@year@ago@1"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_onRELATIVEGT@year@ago@1"

    @pytest.mark.asyncio()
    async def test_more_than(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test more_than date operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "more_than", "field": "sys_updated_on", "value": "@hour@ago@3"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_updated_onMORETHAN@hour@ago@3"

    @pytest.mark.asyncio()
    async def test_changes_from(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test changes_from change detection operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions='[{"operator": "changes_from", "field": "priority", "value": "3"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "priorityCHANGESFROM3"

    @pytest.mark.asyncio()
    async def test_changes_to(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test changes_to change detection operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](conditions='[{"operator": "changes_to", "field": "state", "value": "6"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "stateCHANGESTO6"

    @pytest.mark.asyncio()
    async def test_dynamic(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test dynamic reference operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "dynamic", "field": "cmdb_ci", "value": "javascript:getCIFilter()"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "cmdb_ciDYNAMICjavascript:getCIFilter()"

    @pytest.mark.asyncio()
    async def test_in_hierarchy(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test in_hierarchy reference operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "in_hierarchy", "field": "cmdb_ci", "value": "abc123"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "cmdb_ciIN_HIERARCHYabc123"

    @pytest.mark.asyncio()
    async def test_new_query(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test new_query inserts NQ separator between groups."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "active", "value": "true"},
                {"operator": "new_query", "field": ""},
                {"operator": "equals", "field": "priority", "value": "1"},
            ]
        )
        raw = await tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert "NQ" in result["data"]["query"]

    @pytest.mark.asyncio()
    async def test_rl_query(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test rl_query related list operator."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {
                    "operator": "rl_query",
                    "field": "state",
                    "related_table": "task.incident",
                    "related_field": "state",
                    "rl_operator": "=",
                    "value": "2",
                },
            ]
        )
        raw = await tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert "RLQUERY" in result["data"]["query"]
        assert "ENDRLQUERY" in result["data"]["query"]

    @pytest.mark.asyncio()
    async def test_rl_query_missing_related_table(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """rl_query without related_table returns error."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {
                    "operator": "rl_query",
                    "field": "state",
                    "rl_operator": "=",
                    "value": "2",
                },
            ]
        )
        raw = await tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_time_operator_non_integer_value_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Time operator with non-integer value returns structured error."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": "hours_ago", "field": "sys_created_on", "value": "abc"}]',
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "integer" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_non_string_operator_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Non-string operator value returns type error."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["build_query"](
            conditions='[{"operator": 123, "field": "state"}]',
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "string" in result["error"]["message"].lower()
