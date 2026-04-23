"""Tests for utility functions."""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from servicenow_mcp.errors import ForbiddenError
from servicenow_mcp.utils import (
    ServiceNowQuery,
    resolve_ref_value,
    safe_tool_call,
    sanitize_query_value,
    serialize,
    validate_identifier,
    validate_sys_id,
)
from tests.helpers import decode_response


class TestCorrelationId:
    """Test correlation ID generation."""

    def test_returns_string(self) -> None:
        from servicenow_mcp.utils import generate_correlation_id

        cid = generate_correlation_id()
        assert isinstance(cid, str)

    def test_valid_uuid_format(self) -> None:
        from servicenow_mcp.utils import generate_correlation_id

        cid = generate_correlation_id()
        # Should not raise
        uuid.UUID(cid)

    def test_unique_ids(self) -> None:
        from servicenow_mcp.utils import generate_correlation_id

        ids = {generate_correlation_id() for _ in range(100)}
        assert len(ids) == 100


class TestFormatResponse:
    """Test response formatting."""

    def test_success_envelope(self) -> None:
        from servicenow_mcp.utils import format_response

        raw = format_response(data={"key": "value"}, correlation_id="test-123")
        resp = decode_response(raw)

        assert resp["status"] == "success"
        assert resp["correlation_id"] == "test-123"
        assert resp["data"] == {"key": "value"}

    def test_error_envelope(self) -> None:
        from servicenow_mcp.utils import format_response

        raw = format_response(
            data=None,
            correlation_id="test-456",
            status="error",
            error="Something went wrong",
        )
        resp = decode_response(raw)

        assert resp["status"] == "error"
        assert resp["error"] == {"message": "Something went wrong"}

    def test_pagination_included(self) -> None:
        from servicenow_mcp.utils import format_response

        raw = format_response(
            data=[],
            correlation_id="test-789",
            pagination={"offset": 0, "limit": 100, "total": 250},
        )
        resp = decode_response(raw)

        assert resp["pagination"]["total"] == 250

    def test_warnings_included(self) -> None:
        from servicenow_mcp.utils import format_response

        raw = format_response(
            data={},
            correlation_id="test-999",
            warnings=["Limit capped at 100"],
        )
        resp = decode_response(raw)

        assert "Limit capped at 100" in resp["warnings"]


class TestSerialize:
    """Test serialize function with TOON fallback to JSON."""

    def test_serialize_returns_toon_by_default(self) -> None:
        """When toon_encode succeeds, serialize returns TOON output."""
        result = serialize({"key": "value"})
        # Should be parseable by toon_decode
        parsed = decode_response(result)
        assert parsed["key"] == "value"

    def test_serialize_falls_back_to_json_on_toon_failure(self) -> None:
        """When toon_encode raises, serialize falls back to json.dumps."""
        with patch(
            "servicenow_mcp.utils.toon_encode",
            side_effect=TypeError("unsupported type"),
        ):
            result = serialize({"key": "value"})
        parsed = json.loads(result)
        assert parsed["key"] == "value"

    def test_serialize_fallback_json_is_indented(self) -> None:
        """The JSON fallback uses indent=2."""
        with patch(
            "servicenow_mcp.utils.toon_encode",
            side_effect=RuntimeError("boom"),
        ):
            result = serialize({"a": 1})
        # indent=2 means the output should have newlines and spaces
        assert "\n" in result
        assert "  " in result


class TestServiceNowQuery:
    """Tests for the ServiceNowQuery fluent builder."""

    def test_equals(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().equals("active", "true").build() == "active=true"

    def test_not_equals(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().not_equals("state", "6").build() == "state!=6"

    def test_greater_than(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().greater_than("priority", "3").build() == "priority>3"

    def test_greater_or_equal(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().greater_or_equal("http_status", "400").build() == "http_status>=400"

    def test_less_than(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().less_than("priority", "3").build() == "priority<3"

    def test_less_or_equal(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().less_or_equal("priority", "3").build() == "priority<=3"

    def test_contains(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().contains("script", "GlideRecord").build() == "scriptCONTAINSGlideRecord"

    def test_starts_with(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().starts_with("name", "incident").build() == "nameSTARTSWITHincident"

    def test_like(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().like("source", "incident").build() == "sourceLIKEincident"

    def test_is_empty(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().is_empty("window_end").build() == "window_endISEMPTY"

    def test_is_not_empty(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().is_not_empty("assigned_to").build() == "assigned_toISNOTEMPTY"

    def test_hours_ago(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().hours_ago("sys_created_on", 24).build()
        assert result == "sys_created_on>=javascript:gs.hoursAgoStart(24)"

    def test_minutes_ago(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().minutes_ago("sys_created_on", 60).build()
        assert result == "sys_created_on>=javascript:gs.minutesAgoStart(60)"

    def test_days_ago(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().days_ago("sys_created_on", 30).build()
        assert result == "sys_created_on>=javascript:gs.daysAgoStart(30)"

    def test_older_than_days(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().older_than_days("sys_updated_on", 90).build()
        assert result == "sys_updated_on<=javascript:gs.daysAgoEnd(90)"

    def test_chaining_multiple_conditions(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = (
            ServiceNowQuery().equals("active", "true").equals("priority", "1").hours_ago("sys_created_on", 24).build()
        )
        assert result == "active=true^priority=1^sys_created_on>=javascript:gs.hoursAgoStart(24)"

    def test_raw_fragment(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").raw("ORpriority=1").build()
        assert result == "active=true^ORpriority=1"

    def test_raw_empty_string_ignored(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").raw("").build()
        assert result == "active=true"

    def test_empty_build_returns_empty_string(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        assert ServiceNowQuery().build() == ""

    def test_str_equals_build(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        q = ServiceNowQuery().equals("active", "true").equals("state", "1")
        assert str(q) == q.build()


class TestServiceNowQueryEqualsIf:
    """Test equals_if conditional filter."""

    def test_equals_if_true_condition(self) -> None:
        result = ServiceNowQuery().equals_if("state", "1", True).build()
        assert result == "state=1"

    def test_equals_if_false_condition(self) -> None:
        result = ServiceNowQuery().equals_if("state", "1", False).build()
        assert result == ""

    def test_equals_if_truthy_string(self) -> None:
        result = ServiceNowQuery().equals_if("priority", "2", bool("high")).build()
        assert result == "priority=2"

    def test_equals_if_falsy_empty_string(self) -> None:
        result = ServiceNowQuery().equals_if("priority", "2", bool("")).build()
        assert result == ""

    def test_equals_if_chained(self) -> None:
        result = (
            ServiceNowQuery()
            .equals_if("state", "1", True)
            .equals_if("priority", "2", False)
            .equals_if("assigned_to", "user123", True)
            .build()
        )
        assert result == "state=1^assigned_to=user123"

    def test_equals_if_all_false(self) -> None:
        result = ServiceNowQuery().equals_if("state", "1", False).equals_if("priority", "2", False).build()
        assert result == ""

    def test_equals_if_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().equals_if("bad field!", "1", True)


class TestServiceNowQueryValidation:
    """Test that field-name validation and value sanitization work correctly."""

    def test_invalid_field_name_raises(self) -> None:
        """Field names with invalid characters are rejected."""
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().equals("DROP TABLE", "1")

    def test_invalid_field_uppercase_raises(self) -> None:
        """Uppercase field names are rejected."""
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().equals("Priority", "1")

    def test_invalid_field_special_chars_raises(self) -> None:
        """Special characters in field names are rejected."""
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().contains("field;evil", "val")

    def test_invalid_field_in_is_empty(self) -> None:
        """Null operators also validate field names."""
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().is_empty("bad-field")

    def test_invalid_field_in_is_not_empty(self) -> None:
        """is_not_empty validates field names."""
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().is_not_empty("bad-field")

    def test_dot_walk_field_accepted(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("change_request.number", "CHG001").build()
        assert result == "change_request.number=CHG001"

    def test_dot_walk_multi_level_accepted(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("parent.child.sys_id", "abc123").build()
        assert result == "parent.child.sys_id=abc123"

    def test_dot_walk_leading_dot_rejected(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().equals(".bad", "val")

    def test_dot_walk_trailing_dot_rejected(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().equals("bad.", "val")

    def test_dot_walk_double_dot_rejected(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().equals("a..b", "val")

    def test_caret_in_value_gets_escaped(self) -> None:
        """A caret in a value should be doubled."""
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("description", "a^b").build()
        assert result == "description=a^^b"

    def test_caret_in_contains_value(self) -> None:
        """Value sanitization works in contains()."""
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().contains("script", "x^y").build()
        assert result == "scriptCONTAINSx^^y"

    def test_caret_in_less_than_value(self) -> None:
        """Value sanitization works in less_than()."""
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().less_than("field", "a^b").build()
        assert result == "field<a^^b"

    def test_all_comparison_methods_validate_field(self) -> None:
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

    def test_hours_ago_zero_raises(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="hours must be between 1 and 8760"):
            ServiceNowQuery().hours_ago("sys_created_on", 0)

    def test_hours_ago_negative_raises(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="hours must be between 1 and 8760"):
            ServiceNowQuery().hours_ago("sys_created_on", -5)

    def test_hours_ago_exceeds_max_raises(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="hours must be between 1 and 8760"):
            ServiceNowQuery().hours_ago("sys_created_on", 8761)

    def test_hours_ago_boundary_valid(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        ServiceNowQuery().hours_ago("sys_created_on", 1)
        ServiceNowQuery().hours_ago("sys_created_on", 8760)

    def test_minutes_ago_zero_raises(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="minutes must be between 1 and 525600"):
            ServiceNowQuery().minutes_ago("sys_created_on", 0)

    def test_minutes_ago_exceeds_max_raises(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="minutes must be between 1 and 525600"):
            ServiceNowQuery().minutes_ago("sys_created_on", 525601)

    def test_minutes_ago_boundary_valid(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        ServiceNowQuery().minutes_ago("sys_created_on", 1)
        ServiceNowQuery().minutes_ago("sys_created_on", 525600)

    def test_days_ago_zero_raises(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="days must be between 1 and 365"):
            ServiceNowQuery().days_ago("sys_created_on", 0)

    def test_days_ago_exceeds_max_raises(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="days must be between 1 and 365"):
            ServiceNowQuery().days_ago("sys_created_on", 366)

    def test_days_ago_boundary_valid(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        ServiceNowQuery().days_ago("sys_created_on", 1)
        ServiceNowQuery().days_ago("sys_created_on", 365)

    def test_older_than_days_zero_raises(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="days must be between 1 and 3650"):
            ServiceNowQuery().older_than_days("sys_updated_on", 0)

    def test_older_than_days_exceeds_max_raises(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="days must be between 1 and 3650"):
            ServiceNowQuery().older_than_days("sys_updated_on", 3651)

    def test_older_than_days_boundary_valid(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        ServiceNowQuery().older_than_days("sys_updated_on", 1)
        ServiceNowQuery().older_than_days("sys_updated_on", 3650)

    def test_int_coercion_from_float(self) -> None:
        """Float-ish values should be coerced to int."""
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().hours_ago("sys_created_on", 24).build()  # type: ignore[arg-type]
        assert "24" in result

    def test_time_methods_validate_field(self) -> None:
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


class TestServiceNowQueryRelatedListQuery:
    """Test related list query (RLQUERY) operator."""

    def test_rl_query_basic(self) -> None:
        result = ServiceNowQuery().rl_query("task.incident", "state", "=", "2").build()
        assert result == "RLQUERY" + "task.incident,state,=,2^ENDRLQUERY"

    def test_rl_query_with_other_conditions(self) -> None:
        result = (
            ServiceNowQuery()
            .equals("active", "true")
            .rl_query("task.incident", "state", "=", "2")
            .equals("priority", "1")
            .build()
        )
        assert result == "active=true^RLQUERYtask.incident,state,=,2^ENDRLQUERY^priority=1"

    def test_rl_query_like_operator(self) -> None:
        result = ServiceNowQuery().rl_query("task.incident", "short_description", "LIKE", "error").build()
        assert result == "RLQUERYtask.incident,short_description,LIKE,error^ENDRLQUERY"

    def test_rl_query_validates_related_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().rl_query("task.incident", "bad field!", "=", "2")

    def test_rl_query_sanitizes_value(self) -> None:
        result = ServiceNowQuery().rl_query("task.incident", "state", "=", "a^b").build()
        assert result == "RLQUERYtask.incident,state,=,a^^b^ENDRLQUERY"


class TestServiceNowQueryOrConditions:
    """Test OR condition methods."""

    def test_or_equals(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").or_equals("priority", "1").build()
        assert result == "active=true^ORpriority=1"

    def test_or_starts_with(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").or_starts_with("name", "inc").build()
        assert result == "active=true^ORnameSTARTSWITHinc"

    def test_or_condition_with_contains(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").or_condition("script", "CONTAINS", "test").build()
        assert result == "active=true^ORscriptCONTAINStest"

    def test_or_condition_unknown_operator_raises(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Unknown operator"):
            ServiceNowQuery().or_condition("field", "BADOP", "val")

    def test_or_condition_validates_field(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().or_condition("BAD!", "=", "val")

    def test_or_condition_sanitizes_value(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("a", "1").or_equals("b", "x^y").build()
        assert result == "a=1^ORb=x^^y"


class TestServiceNowQueryOrderBy:
    """Test order_by method."""

    def test_order_by_ascending(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").order_by("sys_created_on").build()
        assert result == "active=true^ORDERBYsys_created_on"

    def test_order_by_descending(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").order_by("sys_created_on", descending=True).build()
        assert result == "active=true^ORDERBYDESCsys_created_on"

    def test_order_by_validates_field(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().order_by("BAD FIELD")

    def test_order_by_standalone(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().order_by("priority").build()
        assert result == "ORDERBYpriority"


class TestServiceNowQueryInList:
    """Test in_list and not_in_list methods."""

    def test_in_list_basic(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().in_list("state", ["1", "2", "3"]).build()
        assert result == "stateIN1,2,3"

    def test_not_in_list_basic(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().not_in_list("state", ["6", "7"]).build()
        assert result == "stateNOT IN6,7"

    def test_in_list_single_value(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().in_list("priority", ["1"]).build()
        assert result == "priorityIN1"

    def test_in_list_validates_field(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().in_list("BAD!", ["1"])

    def test_not_in_list_validates_field(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().not_in_list("BAD!", ["1"])

    def test_in_list_sanitizes_values(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().in_list("description", ["a^b", "c"]).build()
        assert result == "descriptionINa^^b,c"

    def test_not_in_list_sanitizes_values(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().not_in_list("name", ["x^y"]).build()
        assert result == "nameNOT INx^^y"

    def test_in_list_chained(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().equals("active", "true").in_list("state", ["1", "2"]).build()
        assert result == "active=true^stateIN1,2"

    def test_in_list_empty_list(self) -> None:
        from servicenow_mcp.utils import ServiceNowQuery

        result = ServiceNowQuery().in_list("state", []).build()
        assert result == "stateIN"


class TestServiceNowQueryStringOperators:
    """Test extended string operators."""

    def test_ends_with(self) -> None:
        result = ServiceNowQuery().ends_with("email", "@example.com").build()
        assert result == "emailENDSWITH@example.com"

    def test_not_like(self) -> None:
        result = ServiceNowQuery().not_like("short_description", "test").build()
        assert result == "short_descriptionNOT LIKEtest"

    def test_does_not_contain_alias(self) -> None:
        result = ServiceNowQuery().does_not_contain("short_description", "test").build()
        assert result == "short_descriptionNOT LIKEtest"

    def test_between_dates(self) -> None:
        result = ServiceNowQuery().between("sys_created_on", "2026-01-01", "2026-12-31").build()
        assert result == "sys_created_onBETWEEN2026-01-01@2026-12-31"

    def test_between_numbers(self) -> None:
        result = ServiceNowQuery().between("priority", "1", "3").build()
        assert result == "priorityBETWEEN1@3"

    def test_anything(self) -> None:
        result = ServiceNowQuery().anything("state").build()
        assert result == "stateANYTHING"

    def test_empty_string(self) -> None:
        result = ServiceNowQuery().empty_string("description").build()
        assert result == "descriptionEMPTYSTRING"

    def test_ends_with_sanitizes_caret(self) -> None:
        result = ServiceNowQuery().ends_with("name", "a^b").build()
        assert result == "nameENDSWITHa^^b"

    def test_not_like_sanitizes_caret(self) -> None:
        result = ServiceNowQuery().not_like("name", "a^b").build()
        assert result == "nameNOT LIKEa^^b"

    def test_between_sanitizes_carets(self) -> None:
        result = ServiceNowQuery().between("field", "a^b", "c^d").build()
        assert result == "fieldBETWEENa^^b@c^^d"

    def test_ends_with_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().ends_with("bad field!", "val")

    def test_not_like_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().not_like("bad field!", "val")

    def test_between_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().between("bad field!", "a", "b")

    def test_anything_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().anything("bad field!")

    def test_empty_string_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().empty_string("bad field!")

    def test_or_ends_with(self) -> None:
        """Verify new ENDSWITH operator works in or_condition."""
        result = ServiceNowQuery().starts_with("email", "admin").or_condition("email", "ENDSWITH", "@corp.com").build()
        assert result == "emailSTARTSWITHadmin^ORemailENDSWITH@corp.com"

    def test_or_not_like(self) -> None:
        """Verify NOT LIKE operator works in or_condition."""
        result = ServiceNowQuery().contains("name", "dev").or_condition("name", "NOT LIKE", "test").build()
        assert result == "nameCONTAINSdev^ORnameNOT LIKEtest"

    def test_chaining_with_existing(self) -> None:
        """Verify new operators chain correctly with existing ones."""
        result = (
            ServiceNowQuery()
            .equals("active", "true")
            .ends_with("email", "@example.com")
            .not_like("short_description", "test")
            .anything("category")
            .build()
        )
        assert result == "active=true^emailENDSWITH@example.com^short_descriptionNOT LIKEtest^categoryANYTHING"


class TestServiceNowQueryFieldComparison:
    """Test field-to-field comparison operators."""

    def test_gt_field(self) -> None:
        result = ServiceNowQuery().gt_field("sys_updated_on", "sys_created_on").build()
        assert result == "sys_updated_onGT_FIELDsys_created_on"

    def test_lt_field(self) -> None:
        result = ServiceNowQuery().lt_field("priority", "impact").build()
        assert result == "priorityLT_FIELDimpact"

    def test_gt_or_equals_field(self) -> None:
        result = ServiceNowQuery().gt_or_equals_field("end_date", "start_date").build()
        assert result == "end_dateGT_OR_EQUALS_FIELDstart_date"

    def test_lt_or_equals_field(self) -> None:
        result = ServiceNowQuery().lt_or_equals_field("start_date", "end_date").build()
        assert result == "start_dateLT_OR_EQUALS_FIELDend_date"

    def test_same_as(self) -> None:
        result = ServiceNowQuery().same_as("assigned_to", "opened_by").build()
        assert result == "assigned_toSAMEASopened_by"

    def test_not_same_as(self) -> None:
        result = ServiceNowQuery().not_same_as("assigned_to", "opened_by").build()
        assert result == "assigned_toNSAMEASopened_by"

    def test_gt_field_validates_both_fields(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().gt_field("bad field!", "good_field")
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().gt_field("good_field", "bad field!")

    def test_same_as_validates_both_fields(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().same_as("bad field!", "good_field")
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().same_as("good_field", "bad field!")

    def test_chaining_field_comparison(self) -> None:
        result = (
            ServiceNowQuery()
            .equals("active", "true")
            .gt_field("sys_updated_on", "sys_created_on")
            .not_same_as("assigned_to", "opened_by")
            .build()
        )
        assert result == "active=true^sys_updated_onGT_FIELDsys_created_on^assigned_toNSAMEASopened_by"

    def test_or_gt_field(self) -> None:
        """Verify field comparison operators work in or_condition."""
        result = ServiceNowQuery().gt_field("priority", "impact").or_condition("priority", "SAMEAS", "urgency").build()
        assert result == "priorityGT_FIELDimpact^ORprioritySAMEASurgency"


class TestServiceNowQueryReferenceOperators:
    """Test reference and hierarchy operators."""

    def test_dynamic(self) -> None:
        result = ServiceNowQuery().dynamic("cmdb_ci", "javascript:getCIFilter()").build()
        assert result == "cmdb_ciDYNAMICjavascript:getCIFilter()"

    def test_in_hierarchy(self) -> None:
        result = ServiceNowQuery().in_hierarchy("cmdb_ci", "abc123def456").build()
        assert result == "cmdb_ciIN_HIERARCHYabc123def456"

    def test_dynamic_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().dynamic("bad field!", "value")

    def test_in_hierarchy_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().in_hierarchy("bad field!", "value")

    def test_dynamic_sanitizes(self) -> None:
        result = ServiceNowQuery().dynamic("field", "a^b").build()
        assert result == "fieldDYNAMICa^^b"

    def test_or_dynamic(self) -> None:
        result = ServiceNowQuery().dynamic("cmdb_ci", "filter1").or_condition("cmdb_ci", "DYNAMIC", "filter2").build()
        assert result == "cmdb_ciDYNAMICfilter1^ORcmdb_ciDYNAMICfilter2"


class TestServiceNowQueryChangeDetection:
    """Test change detection and NQ operators."""

    def test_val_changes(self) -> None:
        result = ServiceNowQuery().val_changes("state").build()
        assert result == "stateVALCHANGES"

    def test_changes_from(self) -> None:
        result = ServiceNowQuery().changes_from("priority", "3").build()
        assert result == "priorityCHANGESFROM3"

    def test_changes_to(self) -> None:
        result = ServiceNowQuery().changes_to("state", "6").build()
        assert result == "stateCHANGESTO6"

    def test_val_changes_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().val_changes("bad field!")

    def test_changes_from_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().changes_from("bad field!", "3")

    def test_changes_to_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().changes_to("bad field!", "6")

    def test_changes_from_sanitizes(self) -> None:
        result = ServiceNowQuery().changes_from("state", "a^b").build()
        assert result == "stateCHANGESFROMa^^b"

    def test_new_query(self) -> None:
        result = (
            ServiceNowQuery()
            .equals("active", "true")
            .equals("priority", "1")
            .new_query()
            .equals("active", "true")
            .equals("priority", "2")
            .build()
        )
        assert result == "active=true^priority=1^NQ^active=true^priority=2"

    def test_new_query_empty(self) -> None:
        """NQ at the start produces just NQ."""
        result = ServiceNowQuery().new_query().equals("state", "1").build()
        assert result == "NQ^state=1"

    def test_chaining_change_detection(self) -> None:
        result = (
            ServiceNowQuery().val_changes("state").changes_from("priority", "3").changes_to("priority", "1").build()
        )
        assert result == "stateVALCHANGES^priorityCHANGESFROM3^priorityCHANGESTO1"

    def test_or_val_changes(self) -> None:
        result = ServiceNowQuery().val_changes("state").or_condition("priority", "VALCHANGES", "").build()
        assert result == "stateVALCHANGES^ORpriorityVALCHANGES"


class TestServiceNowQueryDateTimeOperators:
    """Test extended date/time operators."""

    def test_on(self) -> None:
        result = ServiceNowQuery().on("sys_created_on", "2026-01-15").build()
        assert result == "sys_created_onON2026-01-15"

    def test_not_on(self) -> None:
        result = ServiceNowQuery().not_on("sys_created_on", "2026-01-15").build()
        assert result == "sys_created_onNOTON2026-01-15"

    def test_relative_gt(self) -> None:
        result = ServiceNowQuery().relative_gt("sys_created_on", "@year@ago@1").build()
        assert result == "sys_created_onRELATIVEGT@year@ago@1"

    def test_relative_lt(self) -> None:
        result = ServiceNowQuery().relative_lt("sys_created_on", "@month@ago@6").build()
        assert result == "sys_created_onRELATIVELT@month@ago@6"

    def test_more_than(self) -> None:
        result = ServiceNowQuery().more_than("sys_updated_on", "@hour@ago@3").build()
        assert result == "sys_updated_onMORETHAN@hour@ago@3"

    def test_datepart(self) -> None:
        result = ServiceNowQuery().datepart("sys_created_on", "dayofweek", "=", "1").build()
        assert result == "sys_created_onDATEPARTdayofweek@=@1"

    def test_datepart_month(self) -> None:
        result = ServiceNowQuery().datepart("sys_created_on", "month", ">", "6").build()
        assert result == "sys_created_onDATEPARTmonth@>@6"

    def test_on_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().on("bad field!", "2026-01-01")

    def test_not_on_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().not_on("bad field!", "2026-01-01")

    def test_relative_gt_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().relative_gt("bad field!", "@year@ago@1")

    def test_more_than_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().more_than("bad field!", "@hour@ago@3")

    def test_datepart_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().datepart("bad field!", "dayofweek", "=", "1")

    def test_on_sanitizes(self) -> None:
        result = ServiceNowQuery().on("field", "a^b").build()
        assert result == "fieldONa^^b"

    def test_chaining_date_operators(self) -> None:
        result = (
            ServiceNowQuery()
            .on("sys_created_on", "2026-01-15")
            .not_on("sys_updated_on", "2026-01-16")
            .relative_gt("closed_at", "@year@ago@1")
            .build()
        )
        assert result == "sys_created_onON2026-01-15^sys_updated_onNOTON2026-01-16^closed_atRELATIVEGT@year@ago@1"

    def test_or_on(self) -> None:
        """Verify ON operator works in or_condition."""
        result = (
            ServiceNowQuery()
            .on("sys_created_on", "2026-01-15")
            .or_condition("sys_created_on", "ON", "2026-01-16")
            .build()
        )
        assert result == "sys_created_onON2026-01-15^ORsys_created_onON2026-01-16"


class TestSafeToolCall:
    """Tests for the safe_tool_call error-handling wrapper."""

    # Tests are async because safe_tool_call awaits the inner fn coroutine.

    async def test_success_passthrough(self) -> None:
        """Successful fn return passes through unchanged."""
        fn = AsyncMock(return_value='{"status": "ok"}')

        result = await safe_tool_call(fn, "test-corr-id")
        assert result == '{"status": "ok"}'

    async def test_forbidden_error_returns_acl_envelope(self) -> None:
        """ForbiddenError is caught and formatted as ACL denial."""

        async def fn() -> str:
            raise ForbiddenError("no access to incident")

        result = await safe_tool_call(fn, "test-corr-id")
        parsed = decode_response(result)
        assert parsed["status"] == "error"
        assert isinstance(parsed["error"], dict)
        assert "Access denied by ServiceNow ACL" in parsed["error"]["message"]
        assert "no access to incident" in parsed["error"]["message"]
        assert parsed["correlation_id"] == "test-corr-id"

    async def test_generic_exception_returns_error_envelope(self) -> None:
        """Generic exceptions are caught and formatted as error envelope."""

        async def fn() -> str:
            raise ValueError("something broke")

        result = await safe_tool_call(fn, "test-corr-id")
        parsed = decode_response(result)
        assert parsed["status"] == "error"
        assert isinstance(parsed["error"], dict)
        assert "something broke" in parsed["error"]["message"]
        assert parsed["correlation_id"] == "test-corr-id"


# ---------------------------------------------------------------------------
# resolve_ref_value
# ---------------------------------------------------------------------------


class TestResolveRefValue:
    """Tests for the resolve_ref_value helper that coerces SN reference fields to strings."""

    def test_string_passthrough(self) -> None:
        """Plain strings are returned unchanged."""
        assert resolve_ref_value("abc123") == "abc123"

    def test_empty_string(self) -> None:
        """Empty strings are returned unchanged."""
        assert resolve_ref_value("") == ""

    def test_dict_with_display_value_only(self) -> None:
        """Dicts with only display_value fall back to it when value is absent."""
        val = {"display_value": "My Workflow", "link": "https://instance.service-now.com/api/..."}
        assert resolve_ref_value(val) == "My Workflow"

    def test_dict_with_value_only(self) -> None:
        """Dicts with only value return it directly."""
        val = {"value": "abc123", "link": "https://instance.service-now.com/api/..."}
        assert resolve_ref_value(val) == "abc123"

    def test_dict_with_neither(self) -> None:
        """Dicts with neither display_value nor value return empty string."""
        val = {"link": "https://instance.service-now.com/api/..."}
        assert resolve_ref_value(val) == ""

    def test_dict_value_preferred_over_display_value(self) -> None:
        """Raw sys_id ('value') takes precedence over 'display_value' for ID-based lookups."""
        val = {"display_value": "Display Name", "value": "sys_id_abc"}
        assert resolve_ref_value(val) == "sys_id_abc"

    def test_none_returns_empty(self) -> None:
        """None is coerced to empty string."""
        assert resolve_ref_value(None) == ""

    def test_integer_returns_str(self) -> None:
        """Integers are coerced via str()."""
        assert resolve_ref_value(42) == "42"

    def test_dict_with_empty_display_value_falls_back(self) -> None:
        """Empty display_value falls through to value key."""
        val = {"display_value": "", "value": "fallback_id"}
        assert resolve_ref_value(val) == "fallback_id"

    def test_dict_with_empty_value_falls_back_to_display_value(self) -> None:
        """Empty value falls through to display_value key."""
        val = {"value": "", "display_value": "Human Label"}
        assert resolve_ref_value(val) == "Human Label"

    def test_resolve_ref_value_prefers_value_over_display_value(self) -> None:
        """Ensure raw sys_id ('value') is preferred over 'display_value' for ID-based lookups."""
        result = resolve_ref_value({"value": "abc123", "display_value": "Human Label"})
        assert result == "abc123"


# ---------------------------------------------------------------------------
# validate_identifier - dict coercion
# ---------------------------------------------------------------------------


class TestValidateIdentifierDictCoercion:
    """Tests that validate_identifier defensively coerces dict reference fields."""

    def test_dict_with_valid_display_value(self) -> None:
        """Dict containing a valid identifier in display_value is accepted."""
        ref = {"display_value": "sys_user", "link": "https://instance.service-now.com/api/..."}
        # Should not raise
        validate_identifier(ref)

    def test_dict_with_invalid_display_value_raises(self) -> None:
        """Dict containing an invalid identifier still raises ValueError."""
        ref = {"display_value": "INVALID IDENTIFIER!", "link": "https://instance.service-now.com/api/..."}
        with pytest.raises(ValueError, match="Invalid identifier"):
            validate_identifier(ref)

    def test_none_raises(self) -> None:
        """None is coerced to empty string which fails validation."""
        with pytest.raises(ValueError, match="Invalid identifier"):
            validate_identifier(None)


# ---------------------------------------------------------------------------
# sanitize_query_value - dict coercion
# ---------------------------------------------------------------------------


class TestSanitizeQueryValueDictCoercion:
    """Tests that sanitize_query_value defensively coerces dict reference fields."""

    def test_dict_with_display_value(self) -> None:
        """Dict containing a display_value is resolved before sanitizing."""
        ref = {"display_value": "some^value", "link": "https://instance.service-now.com/api/..."}
        result = sanitize_query_value(ref)
        assert result == "some^^value"

    def test_dict_without_caret(self) -> None:
        """Dict resolved to a value without carets passes through."""
        ref = {"display_value": "clean_value"}
        result = sanitize_query_value(ref)
        assert result == "clean_value"

    def test_none_returns_empty(self) -> None:
        """None is coerced to empty string."""
        result = sanitize_query_value(None)
        assert result == ""


# ---------------------------------------------------------------------------
# validate_sys_id
# ---------------------------------------------------------------------------


class TestValidateSysId:
    """Tests for validate_sys_id (32-char hex sys_id validation)."""

    def test_valid_sys_id(self) -> None:
        # Should not raise
        validate_sys_id("a" * 32)
        validate_sys_id("0123456789abcdef" * 2)

    def test_invalid_sys_id_too_short(self) -> None:
        with pytest.raises(ValueError, match="Invalid sys_id"):
            validate_sys_id("abc123")

    def test_invalid_sys_id_uppercase(self) -> None:
        with pytest.raises(ValueError, match="Invalid sys_id"):
            validate_sys_id("A" * 32)

    def test_invalid_sys_id_with_special_chars(self) -> None:
        with pytest.raises(ValueError, match="Invalid sys_id"):
            validate_sys_id("a" * 31 + "!")

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError, match="Invalid sys_id"):
            validate_sys_id("")

    @pytest.mark.parametrize(
        "attack",
        [
            "../../etc/passwd",
            "%2e%2e%2f",
            "a" * 31 + "/",
            "/" + "a" * 31,
            "a" * 16 + "/" + "a" * 15,
            "a" * 32 + "/../other",
            "a" * 32 + "?sysparm_fields=password",
            "a" * 32 + "#fragment",
            "a" * 32 + "&admin=true",
            "a" * 32 + "\x00",
            "a" * 32 + " ",
            " " + "a" * 32,
            "a" * 32 + "\n",
        ],
    )
    def test_rejects_path_traversal_and_smuggling(self, attack: str) -> None:
        """validate_sys_id must reject any string containing URL/path metacharacters.

        The 32-hex regex is the single chokepoint that guarantees sys_id values
        can be safely interpolated into REST URL paths. This test asserts the
        regex anchors (``^...$``) and character class hold against the most
        common injection shapes: directory traversal, URL-encoded traversal,
        query-string smuggling, fragment appending, null bytes, and whitespace
        padding.
        """
        with pytest.raises(ValueError, match="Invalid sys_id"):
            validate_sys_id(attack)


# ---------------------------------------------------------------------------
# resolve_query_token - table binding enforcement
# ---------------------------------------------------------------------------


class TestResolveQueryTokenTableBinding:
    """Tests for the table-binding check inside resolve_query_token.

    Phase 4 (MED-1) binds every query token to the table it was built for,
    so a token built against one table cannot be replayed against another.
    """

    def test_matching_table_resolves_to_query(self) -> None:
        """Token whose payload.table matches expected_table returns the query."""
        from servicenow_mcp.state import QueryTokenStore
        from servicenow_mcp.utils import resolve_query_token

        store = QueryTokenStore(ttl_seconds=60)
        token = store.create({"table": "incident", "query": "active=true"})

        resolved = resolve_query_token(token, store, "incident", correlation_id="cid-1")

        assert resolved == "active=true"

    def test_mismatched_table_raises_policy_error(self) -> None:
        """A token built for one table cannot be used against another."""
        from servicenow_mcp.errors import PolicyError
        from servicenow_mcp.state import QueryTokenStore
        from servicenow_mcp.utils import resolve_query_token

        store = QueryTokenStore(ttl_seconds=60)
        # Pretend the caller built a query for sys_properties, then tried to
        # replay it against incident.
        token = store.create({"table": "sys_properties", "query": "name=glide.ui.session_timeout"})

        with pytest.raises(PolicyError, match="built for table 'sys_properties'"):
            resolve_query_token(token, store, "incident", correlation_id="cid-1")

    def test_missing_table_key_raises_policy_error(self) -> None:
        """QueryTokenStore.create rejects payloads that lack a 'table' key.

        This is a hard structural invariant: unbound tokens cannot exist, so
        the table-binding check inside resolve_query_token can never be
        bypassed by a malformed payload reaching a tool call path.
        """
        from servicenow_mcp.state import QueryTokenStore

        store = QueryTokenStore(ttl_seconds=60)

        with pytest.raises(ValueError, match="non-empty 'table' key"):
            store.create({"query": "active=true"})  # no 'table' key

        with pytest.raises(ValueError, match="non-empty 'table' key"):
            store.create({"table": "", "query": "active=true"})  # empty table

    def test_empty_token_returns_empty_string(self) -> None:
        """Empty token is a no-op - resolves to an empty query without table check."""
        from servicenow_mcp.state import QueryTokenStore
        from servicenow_mcp.utils import resolve_query_token

        store = QueryTokenStore(ttl_seconds=60)

        resolved = resolve_query_token("", store, "incident", correlation_id="cid-1")

        assert resolved == ""

    def test_invalid_token_raises_value_error(self) -> None:
        """An unknown or expired token raises ValueError (not PolicyError)."""
        from servicenow_mcp.state import QueryTokenStore
        from servicenow_mcp.utils import resolve_query_token

        store = QueryTokenStore(ttl_seconds=60)

        with pytest.raises(ValueError, match="Invalid or expired query token"):
            resolve_query_token("nonexistent-token", store, "incident", correlation_id="cid-1")
