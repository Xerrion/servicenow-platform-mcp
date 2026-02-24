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
