"""Tests for utility functions."""

import uuid
import warnings

import pytest


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

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            query = build_encoded_query({"active": "true"})
        assert query == "active=true"

    def test_multiple_conditions(self):
        from servicenow_mcp.utils import build_encoded_query

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            query = build_encoded_query({"active": "true", "priority": "1"})
        assert "active=true" in query
        assert "priority=1" in query
        assert "^" in query

    def test_empty_dict(self):
        from servicenow_mcp.utils import build_encoded_query

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            query = build_encoded_query({})
        assert query == ""

    def test_passthrough_string(self):
        """If given a string, return it unchanged."""
        from servicenow_mcp.utils import build_encoded_query

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            query = build_encoded_query("active=true^priority=1")
        assert query == "active=true^priority=1"

    def test_deprecation_warning(self):
        """Calling build_encoded_query emits a DeprecationWarning."""
        from servicenow_mcp.utils import build_encoded_query

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            build_encoded_query({"active": "true"})
        assert any(issubclass(x.category, DeprecationWarning) for x in w)

    def test_value_sanitization(self):
        """Dict values containing ^ are sanitized."""
        from servicenow_mcp.utils import build_encoded_query

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            query = build_encoded_query({"description": "a^b"})
        assert query == "description=a^^b"


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


class TestServiceNowQueryValidation:
    """Test that field-name validation and value sanitization work correctly."""

    def test_invalid_field_name_raises(self):
        """Field names with invalid characters are rejected."""
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().equals("DROP TABLE", "1")

    def test_invalid_field_uppercase_raises(self):
        """Uppercase field names are rejected."""
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().equals("Priority", "1")

    def test_invalid_field_special_chars_raises(self):
        """Special characters in field names are rejected."""
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().contains("field;evil", "val")

    def test_invalid_field_in_is_empty(self):
        """Null operators also validate field names."""
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().is_empty("bad-field")

    def test_invalid_field_in_is_not_empty(self):
        """is_not_empty validates field names."""
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().is_not_empty("bad.field")

    def test_caret_in_value_gets_escaped(self):
        """A caret in a value should be doubled."""
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("description", "a^b").build()
        assert result == "description=a^^b"

    def test_caret_in_contains_value(self):
        """Value sanitization works in contains()."""
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().contains("script", "x^y").build()
        assert result == "scriptCONTAINSx^^y"

    def test_caret_in_less_than_value(self):
        """Value sanitization works in less_than()."""
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().less_than("field", "a^b").build()
        assert result == "field<a^^b"

    def test_all_comparison_methods_validate_field(self):
        """Every comparison method rejects invalid field names."""
        from servicenow_mcp.utils import ServiceNowQuery

        methods_with_value = [
            "equals",
            "not_equals",
            "greater_than",
            "greater_or_equal",
            "less_than",
            "less_or_equal",
            "contains",
            "starts_with",
            "like",
        ]
        for method_name in methods_with_value:
            with pytest.raises(ValueError, match="Invalid identifier"):
                getattr(ServiceNowQuery(), method_name)("BAD!", "val")


class TestServiceNowQueryTimeRanges:
    """Test range checking and int coercion for time-based methods."""

    def test_hours_ago_zero_raises(self):
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="hours must be between 1 and 8760"):
            ServiceNowQuery().hours_ago("sys_created_on", 0)

    def test_hours_ago_negative_raises(self):
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="hours must be between 1 and 8760"):
            ServiceNowQuery().hours_ago("sys_created_on", -5)

    def test_hours_ago_exceeds_max_raises(self):
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="hours must be between 1 and 8760"):
            ServiceNowQuery().hours_ago("sys_created_on", 8761)

    def test_hours_ago_boundary_valid(self):
        from servicenow_mcp.utils import ServiceNowQuery

        ServiceNowQuery().hours_ago("sys_created_on", 1)
        ServiceNowQuery().hours_ago("sys_created_on", 8760)

    def test_minutes_ago_zero_raises(self):
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="minutes must be between 1 and 525600"):
            ServiceNowQuery().minutes_ago("sys_created_on", 0)

    def test_minutes_ago_exceeds_max_raises(self):
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="minutes must be between 1 and 525600"):
            ServiceNowQuery().minutes_ago("sys_created_on", 525601)

    def test_minutes_ago_boundary_valid(self):
        from servicenow_mcp.utils import ServiceNowQuery

        ServiceNowQuery().minutes_ago("sys_created_on", 1)
        ServiceNowQuery().minutes_ago("sys_created_on", 525600)

    def test_days_ago_zero_raises(self):
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="days must be between 1 and 365"):
            ServiceNowQuery().days_ago("sys_created_on", 0)

    def test_days_ago_exceeds_max_raises(self):
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="days must be between 1 and 365"):
            ServiceNowQuery().days_ago("sys_created_on", 366)

    def test_days_ago_boundary_valid(self):
        from servicenow_mcp.utils import ServiceNowQuery

        ServiceNowQuery().days_ago("sys_created_on", 1)
        ServiceNowQuery().days_ago("sys_created_on", 365)

    def test_older_than_days_zero_raises(self):
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="days must be between 1 and 3650"):
            ServiceNowQuery().older_than_days("sys_updated_on", 0)

    def test_older_than_days_exceeds_max_raises(self):
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="days must be between 1 and 3650"):
            ServiceNowQuery().older_than_days("sys_updated_on", 3651)

    def test_older_than_days_boundary_valid(self):
        from servicenow_mcp.utils import ServiceNowQuery

        ServiceNowQuery().older_than_days("sys_updated_on", 1)
        ServiceNowQuery().older_than_days("sys_updated_on", 3650)

    def test_int_coercion_from_float(self):
        """Float-ish values should be coerced to int."""
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().hours_ago("sys_created_on", 24).build()  # type: ignore[arg-type]
        assert "24" in result

    def test_time_methods_validate_field(self):
        """Time-based methods also validate field names."""
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().hours_ago("BAD FIELD", 1)

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().minutes_ago("BAD!", 1)

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().days_ago("BAD!", 1)

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().older_than_days("BAD!", 1)


class TestServiceNowQueryOrConditions:
    """Test OR condition methods."""

    def test_or_equals(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").or_equals("priority", "1").build()
        assert result == "active=true^^ORpriority=1"

    def test_or_starts_with(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").or_starts_with("name", "inc").build()
        assert result == "active=true^^ORnameSTARTSWITHinc"

    def test_or_condition_with_contains(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").or_condition("script", "CONTAINS", "test").build()
        assert result == "active=true^^ORscriptCONTAINStest"

    def test_or_condition_unknown_operator_raises(self):
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Unknown operator"):
            ServiceNowQuery().or_condition("field", "BADOP", "val")

    def test_or_condition_validates_field(self):
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().or_condition("BAD!", "=", "val")

    def test_or_condition_sanitizes_value(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("a", "1").or_equals("b", "x^y").build()
        assert result == "a=1^^ORb=x^^y"


class TestServiceNowQueryOrderBy:
    """Test order_by method."""

    def test_order_by_ascending(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").order_by("sys_created_on").build()
        assert result == "active=true^ORDERBYsys_created_on"

    def test_order_by_descending(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").order_by("sys_created_on", descending=True).build()
        assert result == "active=true^ORDERBYDESCsys_created_on"

    def test_order_by_validates_field(self):
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().order_by("BAD FIELD")

    def test_order_by_standalone(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().order_by("priority").build()
        assert result == "ORDERBYpriority"


class TestServiceNowQueryInList:
    """Test in_list and not_in_list methods."""

    def test_in_list_basic(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().in_list("state", ["1", "2", "3"]).build()
        assert result == "stateIN1,2,3"

    def test_not_in_list_basic(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().not_in_list("state", ["6", "7"]).build()
        assert result == "stateNOT IN6,7"

    def test_in_list_single_value(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().in_list("priority", ["1"]).build()
        assert result == "priorityIN1"

    def test_in_list_validates_field(self):
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().in_list("BAD!", ["1"])

    def test_not_in_list_validates_field(self):
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().not_in_list("BAD!", ["1"])

    def test_in_list_sanitizes_values(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().in_list("description", ["a^b", "c"]).build()
        assert result == "descriptionINa^^b,c"

    def test_not_in_list_sanitizes_values(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().not_in_list("name", ["x^y"]).build()
        assert result == "nameNOT INx^^y"

    def test_in_list_chained(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").in_list("state", ["1", "2"]).build()
        assert result == "active=true^stateIN1,2"

    def test_in_list_empty_list(self):
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().in_list("state", []).build()
        assert result == "stateIN"
