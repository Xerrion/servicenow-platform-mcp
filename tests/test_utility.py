"""Tests for utility tools (build_query)."""

import json

import pytest
from toon_format import decode as toon_decode

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.state import QueryTokenStore


@pytest.fixture
def auth_provider(settings):
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings, auth_provider):
    """Helper: register utility tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.utility import register_tools

    mcp = FastMCP("test")
    query_store = QueryTokenStore()
    mcp._sn_query_store = query_store  # type: ignore[attr-defined]
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


class TestBuildQuery:
    """Tests for the build_query MCP tool."""

    def test_simple_equals(self, settings, auth_provider):
        """Build a simple equals query."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "equals", "field": "active", "value": "true"}]')
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "active=true"
        assert "query_token" in result["data"]
        assert isinstance(result["data"]["query_token"], str)
        assert len(result["data"]["query_token"]) > 0

    def test_with_time_filter(self, settings, auth_provider):
        """Build a query with hours_ago time filter."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "hours_ago", "field": "sys_created_on", "value": 24}]')
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_on>=javascript:gs.hoursAgoStart(24)"
        assert "query_token" in result["data"]
        assert isinstance(result["data"]["query_token"], str)
        assert len(result["data"]["query_token"]) > 0

    def test_multiple_conditions(self, settings, auth_provider):
        """Build a query with multiple conditions."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "active", "value": "true"},
                {"operator": "hours_ago", "field": "sys_created_on", "value": 24},
                {"operator": "like", "field": "source", "value": "incident"},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == (
            "active=true^sys_created_on>=javascript:gs.hoursAgoStart(24)^sourceLIKEincident"
        )
        assert "query_token" in result["data"]
        assert isinstance(result["data"]["query_token"], str)
        assert len(result["data"]["query_token"]) > 0

    def test_is_empty_no_value_needed(self, settings, auth_provider):
        """Unary operators like is_empty don't need a value."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "is_empty", "field": "assigned_to"}]')
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "assigned_toISEMPTY"
        assert "query_token" in result["data"]
        assert isinstance(result["data"]["query_token"], str)
        assert len(result["data"]["query_token"]) > 0

    def test_invalid_operator_returns_error(self, settings, auth_provider):
        """Unknown operator returns an error response."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "INVALID", "field": "active", "value": "true"}]')
        result = toon_decode(raw)
        assert result["status"] == "error"
        assert "Unknown operator" in result["error"]

    def test_invalid_json_returns_error(self, settings, auth_provider):
        """Malformed JSON returns an error response."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions="not valid json")
        result = toon_decode(raw)
        assert result["status"] == "error"
        assert "Invalid JSON" in result["error"]

    def test_missing_field_returns_error(self, settings, auth_provider):
        """Missing required 'field' key returns an error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "equals", "value": "true"}]')
        result = toon_decode(raw)
        assert result["status"] == "error"
        assert "requires 'operator' and 'field'" in result["error"]

    def test_missing_value_for_binary_operator_returns_error(self, settings, auth_provider):
        """Binary operators require a value."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "equals", "field": "active"}]')
        result = toon_decode(raw)
        assert result["status"] == "error"
        assert "requires a 'value'" in result["error"]

    def test_not_array_returns_error(self, settings, auth_provider):
        """Non-array JSON returns an error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='{"operator": "equals"}')
        result = toon_decode(raw)
        assert result["status"] == "error"
        assert "must be a JSON array" in result["error"]

    def test_empty_array_returns_empty_query(self, settings, auth_provider):
        """Empty array returns empty query string."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions="[]")
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == ""
        assert "query_token" in result["data"]

    def test_days_ago_operator(self, settings, auth_provider):
        """Test days_ago time filter."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "days_ago", "field": "sys_created_on", "value": 30}]')
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_on>=javascript:gs.daysAgoStart(30)"
        assert "query_token" in result["data"]

    def test_starts_with_operator(self, settings, auth_provider):
        """Test starts_with string operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "starts_with", "field": "name", "value": "incident"}]')
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "nameSTARTSWITHincident"
        assert "query_token" in result["data"]

    def test_or_equals_operator(self, settings, auth_provider):
        """Test or_equals OR condition."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "state", "value": "1"},
                {"operator": "or_equals", "field": "state", "value": "2"},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "state=1^ORstate=2"
        assert "query_token" in result["data"]

    def test_or_starts_with_operator(self, settings, auth_provider):
        """Test or_starts_with OR condition."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "starts_with", "field": "name", "value": "INC"},
                {"operator": "or_starts_with", "field": "name", "value": "REQ"},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "nameSTARTSWITHINC^ORnameSTARTSWITHREQ"
        assert "query_token" in result["data"]

    def test_in_list_operator(self, settings, auth_provider):
        """Test in_list operator with a list of values."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "in_list", "field": "state", "value": ["1", "2", "3"]},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "stateIN1,2,3"
        assert "query_token" in result["data"]

    def test_not_in_list_operator(self, settings, auth_provider):
        """Test not_in_list operator with a list of values."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "not_in_list", "field": "priority", "value": ["4", "5"]},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "priorityNOT IN4,5"
        assert "query_token" in result["data"]

    def test_in_list_requires_list_value(self, settings, auth_provider):
        """in_list with a non-list value returns an error."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "in_list", "field": "state", "value": "1"},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = toon_decode(raw)
        assert result["status"] == "error"
        assert "list of strings" in result["error"]

    def test_order_by_ascending(self, settings, auth_provider):
        """Test order_by operator (ascending by default)."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "active", "value": "true"},
                {"operator": "order_by", "field": "sys_created_on"},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "active=true^ORDERBYsys_created_on"
        assert "query_token" in result["data"]

    def test_order_by_descending(self, settings, auth_provider):
        """Test order_by operator with descending=true."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "active", "value": "true"},
                {"operator": "order_by", "field": "sys_created_on", "descending": True},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "active=true^ORDERBYDESCsys_created_on"
        assert "query_token" in result["data"]

    def test_value_injection_prevented(self, settings, auth_provider):
        """Value containing ^ is escaped by the builder, preventing injection."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "name", "value": "foo^bar"},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = toon_decode(raw)
        assert result["status"] == "success"
        # The ^ in the value should be escaped to ^^
        assert result["data"]["query"] == "name=foo^^bar"
        assert "query_token" in result["data"]

    def test_hours_ago_missing_value_returns_error(self, settings, auth_provider):
        """Time operator without value key returns error (line 96)."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](
            conditions='[{"operator": "hours_ago", "field": "sys_created_on"}]',
        )
        result = toon_decode(raw)
        assert result["status"] == "error"
        assert "requires an integer 'value'" in result["error"]

    def test_unexpected_exception_returns_error(self, settings, auth_provider):
        """Unexpected exception in ServiceNowQuery triggers generic handler (lines 155-156)."""
        from unittest.mock import patch

        tools = _register_and_get_tools(settings, auth_provider)
        with patch("servicenow_mcp.tools.utility.ServiceNowQuery", side_effect=RuntimeError("boom")):
            raw = tools["build_query"](conditions='[{"operator": "equals", "field": "active", "value": "true"}]')
        result = toon_decode(raw)
        assert result["status"] == "error"
        assert "boom" in result["error"]

    def test_query_token_is_resolvable(self, settings):
        """build_query returns a token that resolves back to the built query."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.utility import register_tools

        query_store = QueryTokenStore()
        mcp = FastMCP("test")
        mcp._sn_query_store = query_store  # type: ignore[attr-defined]
        auth = BasicAuthProvider(settings)
        register_tools(mcp, settings, auth)
        tools = {t.name: t.fn for t in mcp._tool_manager._tools.values()}

        raw = tools["build_query"](conditions='[{"operator": "equals", "field": "active", "value": "true"}]')
        result = toon_decode(raw)
        token = result["data"]["query_token"]

        payload = query_store.get(token)
        assert payload is not None
        assert payload["query"] == "active=true"
