"""Tests for utility functions."""

import uuid


class TestCorrelationId:
    """Test correlation ID generation."""

    def test_returns_string(self):
        from servicenow_mcp.utils import generate_correlation_id

        cid = generate_correlation_id()
        assert isinstance(cid, str)

    def test_valid_uuid_format(self):
        from servicenow_mcp.utils import generate_correlation_id

        cid = generate_correlation_id()
        # Should not raise
        uuid.UUID(cid)

    def test_unique_ids(self):
        from servicenow_mcp.utils import generate_correlation_id

        ids = {generate_correlation_id() for _ in range(100)}
        assert len(ids) == 100


class TestFormatResponse:
    """Test response formatting."""

    def test_success_envelope(self):
        from servicenow_mcp.utils import format_response

        resp = format_response(data={"key": "value"}, correlation_id="test-123")

        assert resp["status"] == "success"
        assert resp["correlation_id"] == "test-123"
        assert resp["data"] == {"key": "value"}

    def test_error_envelope(self):
        from servicenow_mcp.utils import format_response

        resp = format_response(
            data=None,
            correlation_id="test-456",
            status="error",
            error="Something went wrong",
        )

        assert resp["status"] == "error"
        assert resp["error"] == "Something went wrong"

    def test_pagination_included(self):
        from servicenow_mcp.utils import format_response

        resp = format_response(
            data=[],
            correlation_id="test-789",
            pagination={"offset": 0, "limit": 100, "total": 250},
        )

        assert resp["pagination"]["total"] == 250

    def test_warnings_included(self):
        from servicenow_mcp.utils import format_response

        resp = format_response(
            data={},
            correlation_id="test-999",
            warnings=["Limit capped at 100"],
        )

        assert "Limit capped at 100" in resp["warnings"]


class TestBuildEncodedQuery:
    """Test encoded query builder."""

    def test_single_condition(self):
        from servicenow_mcp.utils import build_encoded_query

        query = build_encoded_query({"active": "true"})
        assert query == "active=true"

    def test_multiple_conditions(self):
        from servicenow_mcp.utils import build_encoded_query

        query = build_encoded_query({"active": "true", "priority": "1"})
        assert "active=true" in query
        assert "priority=1" in query
        assert "^" in query

    def test_empty_dict(self):
        from servicenow_mcp.utils import build_encoded_query

        query = build_encoded_query({})
        assert query == ""

    def test_passthrough_string(self):
        """If given a string, return it unchanged."""
        from servicenow_mcp.utils import build_encoded_query

        query = build_encoded_query("active=true^priority=1")
        assert query == "active=true^priority=1"


class TestServiceNowQuery:
    """Tests for the ServiceNowQuery fluent builder."""

    def test_equals(self):
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().equals("active", "true").build() == "active=true"

    def test_not_equals(self):
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().not_equals("state", "6").build() == "state!=6"

    def test_greater_than(self):
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().greater_than("priority", "3").build() == "priority>3"

    def test_greater_or_equal(self):
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().greater_or_equal("http_status", "400").build() == "http_status>=400"

    def test_less_than(self):
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().less_than("priority", "3").build() == "priority<3"

    def test_less_or_equal(self):
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().less_or_equal("priority", "3").build() == "priority<=3"

    def test_contains(self):
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().contains("script", "GlideRecord").build() == "scriptCONTAINSGlideRecord"

    def test_starts_with(self):
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().starts_with("name", "incident").build() == "nameSTARTSWITHincident"

    def test_like(self):
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().like("source", "incident").build() == "sourceLIKEincident"

    def test_is_empty(self):
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().is_empty("window_end").build() == "window_endISEMPTY"

    def test_is_not_empty(self):
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().is_not_empty("assigned_to").build() == "assigned_toISNOTEMPTY"

    def test_hours_ago(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().hours_ago("sys_created_on", 24).build()
        assert result == "sys_created_on>=javascript:gs.hoursAgoStart(24)"

    def test_minutes_ago(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().minutes_ago("sys_created_on", 60).build()
        assert result == "sys_created_on>=javascript:gs.minutesAgoStart(60)"

    def test_days_ago(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().days_ago("sys_created_on", 30).build()
        assert result == "sys_created_on>=javascript:gs.daysAgoStart(30)"

    def test_older_than_days(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().older_than_days("sys_updated_on", 90).build()
        assert result == "sys_updated_on<=javascript:gs.daysAgoEnd(90)"

    def test_chaining_multiple_conditions(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = (
            ServiceNowQuery().equals("active", "true").equals("priority", "1").hours_ago("sys_created_on", 24).build()
        )
        assert result == "active=true^priority=1^sys_created_on>=javascript:gs.hoursAgoStart(24)"

    def test_raw_fragment(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").raw("ORpriority=1").build()
        assert result == "active=true^ORpriority=1"

    def test_raw_empty_string_ignored(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").raw("").build()
        assert result == "active=true"

    def test_empty_build_returns_empty_string(self):
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().build() == ""

    def test_str_equals_build(self):
        from servicenow_mcp.utils import ServiceNowQuery

        q = ServiceNowQuery().equals("active", "true").equals("state", "1")
        assert str(q) == q.build()

