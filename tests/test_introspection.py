"""Tests for introspection tools (table_describe, table_get, table_query, table_aggregate)."""

import json

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.errors import AuthError, ForbiddenError, NotFoundError, ServerError
from servicenow_mcp.policy import DENIED_TABLES

BASE_URL = "https://test.service-now.com"


@pytest.fixture
def auth_provider(settings):
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings, auth_provider):
    """Helper: register introspection tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.introspection import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


class TestTableDescribe:
    """Tests for the table_describe tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_field_metadata(self, settings, auth_provider):
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

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_describe"](table="incident")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["table"] == "incident"
        assert result["data"]["field_count"] == 2
        assert result["data"]["fields"][0]["element"] == "number"
        assert result["data"]["fields"][1]["element"] == "state"

    @pytest.mark.asyncio
    async def test_denied_table_returns_error(self, settings, auth_provider):
        """Blocked tables return an error response (no HTTP call made)."""
        denied = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_describe"](table=denied)
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"].lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_includes_correlation_id(self, settings, auth_provider):
        """Response always contains a correlation_id."""
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(200, json={"result": []})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_describe"](table="incident")
        result = json.loads(raw)

        assert "correlation_id" in result
        assert len(result["correlation_id"]) > 0


class TestTableGet:
    """Tests for the table_get tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_single_record(self, settings, auth_provider):
        """Fetches and returns a single record by sys_id."""
        respx.get(f"{BASE_URL}/api/now/table/incident/abc123").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "abc123", "number": "INC0001", "state": "1"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_get"](table="incident", sys_id="abc123")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == "abc123"
        assert result["data"]["number"] == "INC0001"

    @pytest.mark.asyncio
    @respx.mock
    async def test_masks_sensitive_fields(self, settings, auth_provider):
        """Sensitive fields like password are masked in the response."""
        respx.get(f"{BASE_URL}/api/now/table/sys_user/user1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "user1",
                        "user_name": "admin",
                        "password": "supersecret",
                        "api_key": "key123",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_get"](table="sys_user", sys_id="user1")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["user_name"] == "admin"
        assert result["data"]["password"] == "***MASKED***"
        assert result["data"]["api_key"] == "***MASKED***"

    @pytest.mark.asyncio
    @respx.mock
    async def test_not_found_returns_error(self, settings, auth_provider):
        """404 from ServiceNow produces an error response."""
        respx.get(f"{BASE_URL}/api/now/table/incident/missing").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_get"](table="incident", sys_id="missing")
        result = json.loads(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_denied_table_returns_error(self, settings, auth_provider):
        """Denied table returns error without making HTTP call."""
        denied = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_get"](table=denied, sys_id="abc")
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"].lower()


class TestTableQuery:
    """Tests for the table_query tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_matching_records(self, settings, auth_provider):
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

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_query"](table="incident", query="active=true")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert len(result["data"]) == 2
        assert result["pagination"]["total"] == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_limit_capped_with_warning(self, settings, auth_provider):
        """When requested limit exceeds max_row_limit, it is capped and a warning is added."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        # Default max_row_limit is 100, request 500
        raw = await tools["table_query"](table="incident", query="active=true", limit=500)
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["pagination"]["limit"] == 100
        assert any("capped" in w.lower() for w in result.get("warnings", []))

    @pytest.mark.asyncio
    async def test_large_table_without_date_filter_returns_error(self, settings, auth_provider):
        """Large tables require a date filter; omitting it returns an error."""
        # Add syslog to large tables for this test
        settings.large_table_names_csv = "syslog,sys_audit"
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_query"](table="syslog", query="level=error")
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "date" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_denied_table_returns_error(self, settings, auth_provider):
        """Denied table returns error."""
        denied = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_query"](table=denied, query="active=true")
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"].lower()


class TestTableAggregate:
    """Tests for the table_aggregate tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_aggregate_stats(self, settings, auth_provider):
        """Returns aggregate statistics for the query."""
        respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"stats": {"count": "42"}}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_aggregate"](table="incident", query="active=true")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["stats"]["count"] == "42"

    @pytest.mark.asyncio
    async def test_denied_table_returns_error(self, settings, auth_provider):
        """Denied table returns error."""
        denied = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_aggregate"](table=denied, query="active=true")
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"].lower()


# ── Error propagation tests ──────────────────────────────────────────────


class TestErrorPropagation:
    """Verify that ServiceNowMCPError subclasses raised by the client are caught
    by the tool layer and returned inside a format_response error envelope."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_auth_error_returns_error_envelope(self, settings, auth_provider):
        """AuthError (401) from client is caught and returned in error envelope."""
        respx.get(f"{BASE_URL}/api/now/table/incident/abc123").mock(
            return_value=httpx.Response(
                401,
                json={"error": {"message": "User not authenticated"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_get"](table="incident", sys_id="abc123")
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "not authenticated" in result["error"].lower()
        assert "correlation_id" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_forbidden_error_returns_error_envelope(self, settings, auth_provider):
        """ForbiddenError (403) from client is caught and returned in error envelope."""
        respx.get(f"{BASE_URL}/api/now/table/incident/abc123").mock(
            return_value=httpx.Response(
                403,
                json={"error": {"message": "Insufficient rights to read"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_get"](table="incident", sys_id="abc123")
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "insufficient" in result["error"].lower() or "forbidden" in result["error"].lower()
        assert "correlation_id" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_not_found_error_returns_error_envelope(self, settings, auth_provider):
        """NotFoundError (404) from client is caught and returned in error envelope."""
        respx.get(f"{BASE_URL}/api/now/table/incident/missing").mock(
            return_value=httpx.Response(
                404,
                json={"error": {"message": "Record not found"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_get"](table="incident", sys_id="missing")
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "not found" in result["error"].lower()
        assert "correlation_id" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_server_error_returns_error_envelope(self, settings, auth_provider):
        """ServerError (500) from client is caught and returned in error envelope."""
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(
                500,
                json={"error": {"message": "Internal server error"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_describe"](table="incident")
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "internal server error" in result["error"].lower() or "server error" in result["error"].lower()
        assert "correlation_id" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_query_tool_auth_error_returns_error_envelope(self, settings, auth_provider):
        """AuthError during table_query is caught and returned in error envelope."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                401,
                json={"error": {"message": "Session expired"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_query"](table="incident", query="active=true")
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "session expired" in result["error"].lower() or "authentication" in result["error"].lower()
        assert "correlation_id" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_aggregate_tool_server_error_returns_error_envelope(self, settings, auth_provider):
        """ServerError during table_aggregate is caught and returned in error envelope."""
        respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(
                502,
                json={"error": {"message": "Bad gateway"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_aggregate"](table="incident", query="active=true")
        result = json.loads(raw)

        assert result["status"] == "error"
        assert result["error"]  # Error message is non-empty
        assert "correlation_id" in result
