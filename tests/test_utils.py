"""Tests for utility functions."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from servicenow_mcp.errors import ForbiddenError
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.response import format_response, serialize
from servicenow_mcp.tool_errors import safe_tool_call
from servicenow_mcp.validation import resolve_ref_value, sanitize_query_value, validate_identifier, validate_sys_id
from tests.helpers import decode_response


class TestFormatResponse:
    """Test response formatting."""

    def test_success_envelope(self) -> None:
        raw = format_response(data={"key": "value"})
        resp = decode_response(raw)

        assert resp["status"] == "success"
        assert "correlation_id" not in resp
        assert resp["data"] == {"key": "value"}

    def test_error_envelope(self) -> None:
        raw = format_response(
            data=None,
            status="error",
            error="Something went wrong",
        )
        resp = decode_response(raw)

        assert resp["status"] == "error"
        assert resp["error"] == {"message": "Something went wrong"}

    def test_pagination_included(self) -> None:
        raw = format_response(
            data=[],
            pagination={"offset": 0, "limit": 100, "total": 250},
        )
        resp = decode_response(raw)

        assert resp["pagination"]["total"] == 250

    def test_warnings_included(self) -> None:
        raw = format_response(
            data={},
            warnings=["Limit capped at 100"],
        )
        resp = decode_response(raw)

        assert "Limit capped at 100" in resp["warnings"]

    def test_empty_warnings_omitted_without_pruning_data(self) -> None:
        data = {"empty": "", "missing": None, "zero": 0, "disabled": False, "items": []}
        resp = decode_response(format_response(data=data, warnings=[]))

        assert resp == {"status": "success", "data": data}

    def test_error_and_continuation_metadata_preserved(self) -> None:
        envelope = {
            "data": None,
            "status": "error",
            "error": {"message": "Access denied"},
            "pagination": {"offset": 0, "limit": 5, "total": 20},
            "selection": {"mode": "explicit", "returned_fields": [], "truncated": True},
            "warnings": ["Results truncated", "ACLs limit completeness", "Narrow the filter"],
        }
        resp = decode_response(
            format_response(
                data=envelope["data"],
                status="error",
                error={"message": "Access denied"},
                pagination={"offset": 0, "limit": 5, "total": 20},
                selection={"mode": "explicit", "returned_fields": [], "truncated": True},
                warnings=["Results truncated", "ACLs limit completeness", "Narrow the filter"],
            )
        )
        assert resp == envelope


class TestSerialize:
    """Test serialize function with JSON output and error-envelope fallback."""

    def test_serialize_returns_json_by_default(self) -> None:
        """When json.dumps succeeds, serialize returns parseable JSON output."""
        result = serialize({"key": "value"})
        parsed = decode_response(result)
        assert parsed["key"] == "value"

    def test_serialize_falls_back_to_error_envelope_on_json_failure(self) -> None:
        """When json.dumps raises on the original data, serialize returns a JSON-encoded error envelope.

        The original payload is intentionally NOT leaked through; the failure is
        made visible via the error envelope (logged + reported to Sentry).
        """
        # An arbitrary object that json cannot encode (default=str converts it,
        # so we patch json.dumps to force a TypeError on the first call only).
        original_dumps = json.dumps
        call_count = {"n": 0}

        def faulty_dumps(*args: object, **kwargs: object) -> str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise TypeError("unsupported type")
            return original_dumps(*args, **kwargs)  # type: ignore[arg-type]

        with patch("servicenow_mcp.response.json.dumps", side_effect=faulty_dumps):
            result = serialize({"key": "value"})

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert parsed["error"] == {"message": "Serialization failed"}
        # Original data must NOT appear in the output.
        assert "value" not in result

    def test_serialize_fallback_does_not_promote_record_correlation_id(self) -> None:
        """Serialization failures do not copy record fields into the envelope."""
        original_dumps = json.dumps
        call_count = {"n": 0}

        def faulty_dumps(*args: object, **kwargs: object) -> str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise TypeError("unsupported type")
            return original_dumps(*args, **kwargs)  # type: ignore[arg-type]

        record = {"correlation_id": "record-value", "k": "v"}
        with patch("servicenow_mcp.response.json.dumps", side_effect=faulty_dumps):
            result = serialize(record)

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "correlation_id" not in parsed
        assert parsed["error"] == {"message": "Serialization failed"}

    def test_serialize_fallback_omits_correlation_id_when_absent(self) -> None:
        """When the input has no correlation_id, the envelope must not invent one."""
        original_dumps = json.dumps
        call_count = {"n": 0}

        def faulty_dumps(*args: object, **kwargs: object) -> str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise TypeError("unsupported type")
            return original_dumps(*args, **kwargs)  # type: ignore[arg-type]

        with patch("servicenow_mcp.response.json.dumps", side_effect=faulty_dumps):
            result = serialize({"key": "value"})

        parsed = json.loads(result)
        assert "correlation_id" not in parsed


class TestServiceNowQuery:
    """Tests for the ServiceNowQuery fluent builder."""

    def test_equals(self) -> None:
        assert ServiceNowQuery().equals("active", "true").build() == "active=true"

    def test_greater_or_equal(self) -> None:
        assert ServiceNowQuery().greater_or_equal("http_status", "400").build() == "http_status>=400"

    def test_like(self) -> None:
        assert ServiceNowQuery().like("source", "incident").build() == "sourceLIKEincident"

    def test_is_empty(self) -> None:
        assert ServiceNowQuery().is_empty("window_end").build() == "window_endISEMPTY"

    def test_is_not_empty(self) -> None:
        assert ServiceNowQuery().is_not_empty("assigned_to").build() == "assigned_toISNOTEMPTY"

    def test_hours_ago(self) -> None:
        result = ServiceNowQuery().hours_ago("sys_created_on", 24).build()
        assert result == "sys_created_on>=javascript:gs.hoursAgoStart(24)"

    def test_older_than_days(self) -> None:
        result = ServiceNowQuery().older_than_days("sys_updated_on", 90).build()
        assert result == "sys_updated_on<=javascript:gs.daysAgoEnd(90)"

    def test_chaining_multiple_conditions(self) -> None:
        result = (
            ServiceNowQuery().equals("active", "true").equals("priority", "1").hours_ago("sys_created_on", 24).build()
        )
        assert result == "active=true^priority=1^sys_created_on>=javascript:gs.hoursAgoStart(24)"

    def test_empty_build_returns_empty_string(self) -> None:
        assert ServiceNowQuery().build() == ""

    def test_str_equals_build(self) -> None:
        q = ServiceNowQuery().equals("active", "true").equals("state", "1")
        assert str(q) == q.build()


class TestServiceNowQueryValidation:
    """Test that field-name validation and value sanitization work correctly."""

    def test_invalid_field_name_raises(self) -> None:
        """Field names with invalid characters are rejected."""
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().equals("DROP TABLE", "1")

    def test_invalid_field_uppercase_raises(self) -> None:
        """Uppercase field names are rejected."""
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().equals("Priority", "1")

    def test_invalid_field_in_is_empty(self) -> None:
        """Null operators also validate field names."""
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().is_empty("bad-field")

    def test_invalid_field_in_is_not_empty(self) -> None:
        """is_not_empty validates field names."""
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().is_not_empty("bad-field")

    def test_dot_walk_field_accepted(self) -> None:
        result = ServiceNowQuery().equals("change_request.number", "CHG001").build()
        assert result == "change_request.number=CHG001"

    def test_dot_walk_multi_level_accepted(self) -> None:
        result = ServiceNowQuery().equals("parent.child.sys_id", "abc123").build()
        assert result == "parent.child.sys_id=abc123"

    def test_dot_walk_leading_dot_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().equals(".bad", "val")

    def test_dot_walk_trailing_dot_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().equals("bad.", "val")

    def test_dot_walk_double_dot_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().equals("a..b", "val")

    def test_caret_in_value_gets_escaped(self) -> None:
        """A caret in a value should be doubled."""
        result = ServiceNowQuery().equals("description", "a^b").build()
        assert result == "description=a^^b"

    def test_all_comparison_methods_validate_field(self) -> None:
        """Every comparison method rejects invalid field names."""
        methods_with_value = [
            "equals",
            "greater_or_equal",
            "like",
        ]
        for method_name in methods_with_value:
            with pytest.raises(ValueError, match="Invalid identifier"):
                getattr(ServiceNowQuery(), method_name)("BAD!", "val")


class TestServiceNowQueryTimeRanges:
    """Test range checking and int coercion for time-based methods."""

    def test_hours_ago_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="hours must be between 1 and 8760"):
            ServiceNowQuery().hours_ago("sys_created_on", 0)

    def test_hours_ago_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="hours must be between 1 and 8760"):
            ServiceNowQuery().hours_ago("sys_created_on", -5)

    def test_hours_ago_exceeds_max_raises(self) -> None:
        with pytest.raises(ValueError, match="hours must be between 1 and 8760"):
            ServiceNowQuery().hours_ago("sys_created_on", 8761)

    def test_hours_ago_boundary_valid(self) -> None:
        ServiceNowQuery().hours_ago("sys_created_on", 1)
        ServiceNowQuery().hours_ago("sys_created_on", 8760)

    def test_older_than_days_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="days must be between 1 and 3650"):
            ServiceNowQuery().older_than_days("sys_updated_on", 0)

    def test_older_than_days_exceeds_max_raises(self) -> None:
        with pytest.raises(ValueError, match="days must be between 1 and 3650"):
            ServiceNowQuery().older_than_days("sys_updated_on", 3651)

    def test_older_than_days_boundary_valid(self) -> None:
        ServiceNowQuery().older_than_days("sys_updated_on", 1)
        ServiceNowQuery().older_than_days("sys_updated_on", 3650)

    def test_int_coercion_from_float(self) -> None:
        """Float-ish values should be coerced to int."""
        result = ServiceNowQuery().hours_ago("sys_created_on", 24).build()  # type: ignore[arg-type]
        assert "24" in result


class TestServiceNowQueryOrConditions:
    """Test OR condition methods."""

    def test_or_equals(self) -> None:
        result = ServiceNowQuery().equals("active", "true").or_equals("priority", "1").build()
        assert result == "active=true^ORpriority=1"

    def test_or_starts_with(self) -> None:
        result = ServiceNowQuery().equals("active", "true").or_starts_with("name", "inc").build()
        assert result == "active=true^ORnameSTARTSWITHinc"

    def test_or_condition_with_contains(self) -> None:
        result = ServiceNowQuery().equals("active", "true").or_condition("script", "CONTAINS", "test").build()
        assert result == "active=true^ORscriptCONTAINStest"

    def test_or_condition_unknown_operator_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown operator"):
            ServiceNowQuery().or_condition("field", "BADOP", "val")

    def test_or_condition_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().or_condition("BAD!", "=", "val")

    def test_or_condition_sanitizes_value(self) -> None:
        result = ServiceNowQuery().equals("a", "1").or_equals("b", "x^y").build()
        assert result == "a=1^ORb=x^^y"


class TestServiceNowQueryOrderBy:
    """Test order_by method."""

    def test_order_by_ascending(self) -> None:
        result = ServiceNowQuery().equals("active", "true").order_by("sys_created_on").build()
        assert result == "active=true^ORDERBYsys_created_on"

    def test_order_by_descending(self) -> None:
        result = ServiceNowQuery().equals("active", "true").order_by("sys_created_on", descending=True).build()
        assert result == "active=true^ORDERBYDESCsys_created_on"

    def test_order_by_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().order_by("BAD FIELD")

    def test_order_by_standalone(self) -> None:
        result = ServiceNowQuery().order_by("priority").build()
        assert result == "ORDERBYpriority"


class TestServiceNowQueryInList:
    """Test in_list and not_in_list methods."""

    def test_in_list_basic(self) -> None:
        result = ServiceNowQuery().in_list("state", ["1", "2", "3"]).build()
        assert result == "stateIN1,2,3"

    def test_in_list_single_value(self) -> None:
        result = ServiceNowQuery().in_list("priority", ["1"]).build()
        assert result == "priorityIN1"

    def test_in_list_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().in_list("BAD!", ["1"])

    def test_in_list_sanitizes_values(self) -> None:
        result = ServiceNowQuery().in_list("description", ["a^b", "c"]).build()
        assert result == "descriptionINa^^b,c"

    def test_in_list_chained(self) -> None:
        result = ServiceNowQuery().equals("active", "true").in_list("state", ["1", "2"]).build()
        assert result == "active=true^stateIN1,2"

    def test_in_list_empty_list(self) -> None:
        result = ServiceNowQuery().in_list("state", []).build()
        assert result == "stateIN"


class TestServiceNowQueryChangeDetection:
    """Test change detection and NQ operators."""

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


class TestSafeToolCall:
    """Tests for the safe_tool_call error-handling wrapper."""

    # Tests are async because safe_tool_call awaits the inner fn coroutine.

    async def test_success_passthrough(self) -> None:
        """Successful fn return passes through unchanged."""
        fn = AsyncMock(return_value='{"status": "ok"}')

        result = await safe_tool_call(fn)
        assert result == '{"status": "ok"}'

    async def test_acl_error_returns_acl_envelope(self) -> None:
        """ACLError is caught and formatted as ACL denial."""
        from servicenow_mcp.errors import ACLError

        async def fn() -> str:
            raise ACLError("ACL blocked incident")

        result = await safe_tool_call(fn)
        parsed = decode_response(result)
        assert parsed["status"] == "error"
        assert isinstance(parsed["error"], dict)
        assert "Access denied by ServiceNow ACL" in parsed["error"]["message"]
        assert "ACL blocked incident" in parsed["error"]["message"]
        assert "correlation_id" not in parsed

    async def test_forbidden_error_returns_forbidden_envelope(self) -> None:
        """ForbiddenError is caught and formatted as generic forbidden."""

        async def fn() -> str:
            raise ForbiddenError("insufficient role")

        result = await safe_tool_call(fn)
        parsed = decode_response(result)
        assert parsed["status"] == "error"
        assert isinstance(parsed["error"], dict)
        assert "Access forbidden by ServiceNow" in parsed["error"]["message"]
        assert "insufficient role" in parsed["error"]["message"]
        assert "Access denied by ServiceNow ACL" not in parsed["error"]["message"]
        assert "correlation_id" not in parsed

    async def test_generic_exception_returns_error_envelope(self) -> None:
        """Truly unclassified exceptions are returned as an opaque envelope.

        The original ``str(exc)`` is NOT exposed to the caller — it could leak
        internal hostnames, paths, and stack fragments. The full exception is
        logged locally instead. ``ValueError`` and ``ServiceNowMCPError`` have
        their own arms with verbose messages — see the curated-error tests.
        """

        async def fn() -> str:
            raise RuntimeError("something broke")

        result = await safe_tool_call(fn)
        parsed = decode_response(result)
        assert parsed["status"] == "error"
        assert isinstance(parsed["error"], dict)
        message = parsed["error"]["message"]
        assert "something broke" not in message
        assert message == "Internal error"
        assert "correlation_id" not in parsed

    async def test_get_timeout_returns_query_narrowing_advice(self) -> None:
        async def fn() -> str:
            raise httpx.ReadTimeout(
                "private request details",
                request=httpx.Request("GET", "https://test.service-now.com/api/now/table/incident"),
            )

        result = await safe_tool_call(fn)
        parsed = decode_response(result)
        message = parsed["error"]["message"]

        assert parsed["status"] == "error"
        assert "timed out" in message
        assert "narrow" in message.lower()
        assert "Lowering limit only reduces returned rows" in message
        assert "private request details" not in message

    @pytest.mark.parametrize(
        ("method", "timeout_type", "phase"),
        [
            ("POST", httpx.ReadTimeout, "receiving the response"),
            ("PATCH", httpx.WriteTimeout, "sending the request"),
            ("DELETE", httpx.ConnectTimeout, "connecting"),
        ],
    )
    async def test_mutation_timeout_requires_verification_before_retry(
        self,
        method: str,
        timeout_type: type[httpx.TimeoutException],
        phase: str,
    ) -> None:
        async def fn() -> str:
            raise timeout_type(
                "private request details",
                request=httpx.Request(method, "https://test.service-now.com/api/now/table/incident"),
            )

        result = await safe_tool_call(fn)
        message = decode_response(result)["error"]["message"]

        assert method in message
        assert phase in message
        assert "remote outcome is unknown" in message
        assert "Verify the remote outcome before retrying" in message
        assert "narrow" not in message.lower()
        assert "private request details" not in message

    async def test_timeout_without_request_has_no_query_advice(self) -> None:
        async def fn() -> str:
            raise httpx.PoolTimeout("private request details")

        message = decode_response(await safe_tool_call(fn))["error"]["message"]

        assert "waiting for a connection" in message
        assert "narrow" not in message.lower()
        assert "private request details" not in message


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
