"""Tests for utility tools (build_query)."""

import json

import pytest

from servicenow_mcp.auth import BasicAuthProvider


@pytest.fixture
def auth_provider(settings):
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings, auth_provider):
    """Helper: register utility tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.utility import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


class TestBuildQuery:
    """Tests for the build_query MCP tool."""

    def test_simple_equals(self, settings, auth_provider):
        """Build a simple equals query."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "equals", "field": "active", "value": "true"}]')
        result = json.loads(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "active=true"

    def test_with_time_filter(self, settings, auth_provider):
        """Build a query with hours_ago time filter."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "hours_ago", "field": "sys_created_on", "value": 24}]')
        result = json.loads(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_on>=javascript:gs.hoursAgoStart(24)"

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
        result = json.loads(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == (
            "active=true^sys_created_on>=javascript:gs.hoursAgoStart(24)^sourceLIKEincident"
        )

    def test_is_empty_no_value_needed(self, settings, auth_provider):
        """Unary operators like is_empty don't need a value."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "is_empty", "field": "assigned_to"}]')
        result = json.loads(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "assigned_toISEMPTY"

    def test_invalid_operator_returns_error(self, settings, auth_provider):
        """Unknown operator returns an error response."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "INVALID", "field": "active", "value": "true"}]')
        result = json.loads(raw)
        assert result["status"] == "error"
        assert "Unknown operator" in result["error"]

    def test_invalid_json_returns_error(self, settings, auth_provider):
        """Malformed JSON returns an error response."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions="not valid json")
        result = json.loads(raw)
        assert result["status"] == "error"
        assert "Invalid JSON" in result["error"]

    def test_missing_field_returns_error(self, settings, auth_provider):
        """Missing required 'field' key returns an error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "equals", "value": "true"}]')
        result = json.loads(raw)
        assert result["status"] == "error"
        assert "requires 'operator' and 'field'" in result["error"]

    def test_missing_value_for_binary_operator_returns_error(self, settings, auth_provider):
        """Binary operators require a value."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "equals", "field": "active"}]')
        result = json.loads(raw)
        assert result["status"] == "error"
        assert "requires a 'value'" in result["error"]

    def test_not_array_returns_error(self, settings, auth_provider):
        """Non-array JSON returns an error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='{"operator": "equals"}')
        result = json.loads(raw)
        assert result["status"] == "error"
        assert "must be a JSON array" in result["error"]

    def test_empty_array_returns_empty_query(self, settings, auth_provider):
        """Empty array returns empty query string."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions="[]")
        result = json.loads(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == ""

    def test_days_ago_operator(self, settings, auth_provider):
        """Test days_ago time filter."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "days_ago", "field": "sys_created_on", "value": 30}]')
        result = json.loads(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_on>=javascript:gs.daysAgoStart(30)"

    def test_starts_with_operator(self, settings, auth_provider):
        """Test starts_with string operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "starts_with", "field": "name", "value": "incident"}]')
        result = json.loads(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "nameSTARTSWITHincident"

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
        result = json.loads(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "state=1^^ORstate=2"

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
        result = json.loads(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "nameSTARTSWITHINC^^ORnameSTARTSWITHREQ"

    def test_in_list_operator(self, settings, auth_provider):
        """Test in_list operator with a list of values."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "in_list", "field": "state", "value": ["1", "2", "3"]},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = json.loads(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "stateIN1,2,3"

    def test_not_in_list_operator(self, settings, auth_provider):
        """Test not_in_list operator with a list of values."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "not_in_list", "field": "priority", "value": ["4", "5"]},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = json.loads(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "priorityNOT IN4,5"

    def test_in_list_requires_list_value(self, settings, auth_provider):
        """in_list with a non-list value returns an error."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "in_list", "field": "state", "value": "1"},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = json.loads(raw)
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
        result = json.loads(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "active=true^ORDERBYsys_created_on"

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
        result = json.loads(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "active=true^ORDERBYDESCsys_created_on"

    def test_value_injection_prevented(self, settings, auth_provider):
        """Value containing ^ is escaped by the builder, preventing injection."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "name", "value": "foo^bar"},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = json.loads(raw)
        assert result["status"] == "success"
        # The ^ in the value should be escaped to ^^
        assert result["data"]["query"] == "name=foo^^bar"

    def test_hours_ago_missing_value_returns_error(self, settings, auth_provider):
        """Time operator without value key returns error (line 96)."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](
            conditions='[{"operator": "hours_ago", "field": "sys_created_on"}]',
        )
        result = json.loads(raw)
        assert result["status"] == "error"
        assert "requires an integer 'value'" in result["error"]

    def test_unexpected_exception_returns_error(self, settings, auth_provider):
        """Unexpected exception in ServiceNowQuery triggers generic handler (lines 155-156)."""
        from unittest.mock import patch

        tools = _register_and_get_tools(settings, auth_provider)
        with patch("servicenow_mcp.tools.utility.ServiceNowQuery", side_effect=RuntimeError("boom")):
            raw = tools["build_query"](conditions='[{"operator": "equals", "field": "active", "value": "true"}]')
        result = json.loads(raw)
        assert result["status"] == "error"
        assert "boom" in result["error"]
