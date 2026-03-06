"""Tests for utility tools (build_query)."""

import json
from typing import Any

import pytest

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.mcp_state import attach_query_store
from servicenow_mcp.state import QueryTokenStore
from tests.helpers import decode_response, get_tool_functions


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings: Any, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Helper: register utility tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.utility import register_tools

    mcp = FastMCP("test")
    query_store = QueryTokenStore()
    attach_query_store(mcp, query_store)
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


class TestBuildQuery:
    """Tests for the build_query MCP tool."""

    def test_simple_equals(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Build a simple equals query."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "equals", "field": "active", "value": "true"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "active=true"
        assert "query_token" in result["data"]
        assert isinstance(result["data"]["query_token"], str)
        assert len(result["data"]["query_token"]) > 0

    def test_with_time_filter(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Build a query with hours_ago time filter."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "hours_ago", "field": "sys_created_on", "value": 24}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_on>=javascript:gs.hoursAgoStart(24)"
        assert "query_token" in result["data"]
        assert isinstance(result["data"]["query_token"], str)
        assert len(result["data"]["query_token"]) > 0

    def test_multiple_conditions(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Build a query with multiple conditions."""
        tools = _register_and_get_tools(settings, auth_provider)
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
        raw = tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == (
            "active=true^sys_created_on>=javascript:gs.hoursAgoStart(24)^sourceLIKEincident"
        )
        assert "query_token" in result["data"]
        assert isinstance(result["data"]["query_token"], str)
        assert len(result["data"]["query_token"]) > 0

    def test_is_empty_no_value_needed(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Unary operators like is_empty don't need a value."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "is_empty", "field": "assigned_to"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "assigned_toISEMPTY"
        assert "query_token" in result["data"]
        assert isinstance(result["data"]["query_token"], str)
        assert len(result["data"]["query_token"]) > 0

    def test_invalid_operator_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Unknown operator returns an error response."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "INVALID", "field": "active", "value": "true"}]')
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "Unknown operator" in result["error"]["message"]

    def test_invalid_json_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Malformed JSON returns an error response."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions="not valid json")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "Invalid JSON" in result["error"]["message"]

    def test_missing_field_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Missing required 'field' key returns an error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "equals", "value": "true"}]')
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "requires a 'field'" in result["error"]["message"]

    def test_missing_value_for_binary_operator_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Binary operators require a value."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "equals", "field": "active"}]')
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "requires a 'value'" in result["error"]["message"]

    def test_not_array_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Non-array JSON returns an error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='{"operator": "equals"}')
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "must be a JSON array" in result["error"]["message"]

    def test_empty_array_returns_empty_query(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Empty array returns empty query string."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions="[]")
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == ""
        assert "query_token" in result["data"]

    def test_days_ago_operator(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test days_ago time filter."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "days_ago", "field": "sys_created_on", "value": 30}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_on>=javascript:gs.daysAgoStart(30)"
        assert "query_token" in result["data"]

    def test_starts_with_operator(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test starts_with string operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "starts_with", "field": "name", "value": "incident"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "nameSTARTSWITHincident"
        assert "query_token" in result["data"]

    def test_or_equals_operator(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test or_equals OR condition."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "state", "value": "1"},
                {"operator": "or_equals", "field": "state", "value": "2"},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "state=1^ORstate=2"
        assert "query_token" in result["data"]

    def test_or_starts_with_operator(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test or_starts_with OR condition."""
        tools = _register_and_get_tools(settings, auth_provider)
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
        raw = tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "nameSTARTSWITHINC^ORnameSTARTSWITHREQ"
        assert "query_token" in result["data"]

    def test_in_list_operator(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test in_list operator with a list of values."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {
                    "operator": "in_list",
                    "field": "state",
                    "value": ["1", "2", "3"],
                },
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "stateIN1,2,3"
        assert "query_token" in result["data"]

    def test_not_in_list_operator(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test not_in_list operator with a list of values."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {
                    "operator": "not_in_list",
                    "field": "priority",
                    "value": ["4", "5"],
                },
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "priorityNOT IN4,5"
        assert "query_token" in result["data"]

    def test_in_list_requires_list_value(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """in_list with a non-list value returns an error."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "in_list", "field": "state", "value": "1"},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "list of strings" in result["error"]["message"]

    def test_order_by_ascending(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test order_by operator (ascending by default)."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "active", "value": "true"},
                {"operator": "order_by", "field": "sys_created_on"},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "active=true^ORDERBYsys_created_on"
        assert "query_token" in result["data"]

    def test_order_by_descending(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test order_by operator with descending=true."""
        tools = _register_and_get_tools(settings, auth_provider)
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
        raw = tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "active=true^ORDERBYDESCsys_created_on"
        assert "query_token" in result["data"]

    def test_value_injection_prevented(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Value containing ^ is escaped by the builder, preventing injection."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "name", "value": "foo^bar"},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        # The ^ in the value should be escaped to ^^
        assert result["data"]["query"] == "name=foo^^bar"
        assert "query_token" in result["data"]

    def test_hours_ago_missing_value_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Time operator without value key returns error (line 96)."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](
            conditions='[{"operator": "hours_ago", "field": "sys_created_on"}]',
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "requires an integer 'value'" in result["error"]["message"]

    def test_unexpected_exception_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Unexpected exception in ServiceNowQuery triggers generic handler (lines 155-156)."""
        from unittest.mock import patch

        tools = _register_and_get_tools(settings, auth_provider)
        with patch(
            "servicenow_mcp.tools.utility.ServiceNowQuery",
            side_effect=RuntimeError("boom"),
        ):
            raw = tools["build_query"](conditions='[{"operator": "equals", "field": "active", "value": "true"}]')
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "boom" in result["error"]["message"]

    def test_query_token_is_resolvable(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """build_query returns a token that resolves back to the built query."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.utility import register_tools

        query_store = QueryTokenStore()
        mcp = FastMCP("test")
        attach_query_store(mcp, query_store)
        register_tools(mcp, settings, auth_provider)
        tools = get_tool_functions(mcp)

        raw = tools["build_query"](conditions='[{"operator": "equals", "field": "active", "value": "true"}]')
        result = decode_response(raw)
        token = result["data"]["query_token"]

        payload = query_store.get(token)
        assert payload is not None
        assert payload["query"] == "active=true"

    # -- New operator tests (Phase 9) ------------------------------------------

    def test_ends_with(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test ends_with string operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "ends_with", "field": "email", "value": "@example.com"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "emailENDSWITH@example.com"

    def test_not_like(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test not_like string operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "not_like", "field": "name", "value": "test"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "nameNOT LIKEtest"

    def test_anything(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test anything unary operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "anything", "field": "state"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "stateANYTHING"

    def test_empty_string(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test empty_string unary operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "empty_string", "field": "description"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "descriptionEMPTYSTRING"

    def test_val_changes(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test val_changes unary operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "val_changes", "field": "state"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "stateVALCHANGES"

    def test_gt_field(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test gt_field comparison operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](
            conditions='[{"operator": "gt_field", "field": "sys_updated_on", "other_field": "sys_created_on"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_updated_onGT_FIELDsys_created_on"

    def test_same_as(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test same_as field comparison operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](
            conditions='[{"operator": "same_as", "field": "assigned_to", "other_field": "opened_by"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "assigned_toSAMEASopened_by"

    def test_field_operator_with_value_fallback(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Field operators should accept 'value' as fallback for 'other_field'."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "lt_field", "field": "priority", "value": "impact"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "priorityLT_FIELDimpact"

    def test_field_operator_missing_other_field(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Field operators without other_field or value return error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "gt_field", "field": "priority"}]')
        result = decode_response(raw)
        assert result["status"] == "error"

    def test_between(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test between range operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](
            conditions='[{"operator": "between", "field": "sys_created_on", "start": "2026-01-01", "end": "2026-12-31"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_onBETWEEN2026-01-01@2026-12-31"

    def test_between_missing_end(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """between without end value returns error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](
            conditions='[{"operator": "between", "field": "sys_created_on", "start": "2026-01-01"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "error"

    def test_datepart(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test datepart operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](
            conditions='[{"operator": "datepart", "field": "sys_created_on", "part": "dayofweek", "dp_operator": "=", "dp_value": "1"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_onDATEPARTdayofweek@=@1"

    def test_datepart_missing_part(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """datepart without required params returns error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "datepart", "field": "sys_created_on"}]')
        result = decode_response(raw)
        assert result["status"] == "error"

    def test_on(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test on date operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "on", "field": "sys_created_on", "value": "2026-01-15"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_onON2026-01-15"

    def test_relative_gt(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test relative_gt date operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](
            conditions='[{"operator": "relative_gt", "field": "sys_created_on", "value": "@year@ago@1"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_created_onRELATIVEGT@year@ago@1"

    def test_more_than(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test more_than date operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](
            conditions='[{"operator": "more_than", "field": "sys_updated_on", "value": "@hour@ago@3"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "sys_updated_onMORETHAN@hour@ago@3"

    def test_changes_from(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test changes_from change detection operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "changes_from", "field": "priority", "value": "3"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "priorityCHANGESFROM3"

    def test_changes_to(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test changes_to change detection operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "changes_to", "field": "state", "value": "6"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "stateCHANGESTO6"

    def test_dynamic(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test dynamic reference operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](
            conditions='[{"operator": "dynamic", "field": "cmdb_ci", "value": "javascript:getCIFilter()"}]'
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "cmdb_ciDYNAMICjavascript:getCIFilter()"

    def test_in_hierarchy(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test in_hierarchy reference operator."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](conditions='[{"operator": "in_hierarchy", "field": "cmdb_ci", "value": "abc123"}]')
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["query"] == "cmdb_ciIN_HIERARCHYabc123"

    def test_new_query(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test new_query inserts NQ separator between groups."""
        tools = _register_and_get_tools(settings, auth_provider)
        conditions = json.dumps(
            [
                {"operator": "equals", "field": "active", "value": "true"},
                {"operator": "new_query", "field": ""},
                {"operator": "equals", "field": "priority", "value": "1"},
            ]
        )
        raw = tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert "NQ" in result["data"]["query"]

    def test_rl_query(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Test rl_query related list operator."""
        tools = _register_and_get_tools(settings, auth_provider)
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
        raw = tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert "RLQUERY" in result["data"]["query"]
        assert "ENDRLQUERY" in result["data"]["query"]

    def test_rl_query_missing_related_table(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """rl_query without related_table returns error."""
        tools = _register_and_get_tools(settings, auth_provider)
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
        raw = tools["build_query"](conditions=conditions)
        result = decode_response(raw)
        assert result["status"] == "error"

    def test_time_operator_non_integer_value_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Time operator with non-integer value returns structured error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](
            conditions='[{"operator": "hours_ago", "field": "sys_created_on", "value": "abc"}]',
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "integer" in result["error"]["message"].lower()

    def test_non_string_operator_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Non-string operator value returns type error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = tools["build_query"](
            conditions='[{"operator": 123, "field": "state"}]',
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "string" in result["error"]["message"].lower()
