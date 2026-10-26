"""Tests for utility functions."""

from typing import Never

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import result as operation_result
from servicenow_mcp.mcp.errors import safe_tool_call
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.validation import (
    resolve_ref_value,
    sanitize_query_value,
    validate_identifier,
)
from tests.helpers import decode_response


class TestNativeResults:
    def test_success_and_metadata_omissions(self):
        data = {"empty": "", "missing": None, "zero": 0, "disabled": False, "items": []}
        assert operation_result(data=data, warnings=[], truncation={}).to_payload() == {
            "status": "success",
            "data": data,
        }


class TestServiceNowQuery:
    """Tests for the ServiceNowQuery fluent builder."""

    def test_str_equals_build(self) -> None:
        q = ServiceNowQuery().equals("active", "true").equals("state", "1")
        assert str(q) == q.build()


class TestServiceNowQueryValidation:
    """Test that field-name validation and value sanitization work correctly."""

    def test_invalid_field_in_is_empty(self) -> None:
        """Null operators also validate field names."""
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().is_empty("bad-field")

    def test_invalid_field_in_is_not_empty(self) -> None:
        """is_not_empty validates field names."""
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().is_not_empty("bad-field")

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

    def test_older_than_days_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="days must be between 1 and 3650"):
            ServiceNowQuery().older_than_days("sys_updated_on", 0)


class TestServiceNowQueryOrConditions:
    """Test OR condition methods."""

    def test_or_condition_unknown_operator_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown operator"):
            ServiceNowQuery().or_condition("field", "BADOP", "val")

    def test_or_condition_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().or_condition("BAD!", "=", "val")


class TestServiceNowQueryOrderBy:
    """Test order_by method."""

    def test_order_by_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().order_by("BAD FIELD")


class TestServiceNowQueryInList:
    """Test in_list and not_in_list methods."""

    def test_in_list_validates_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            ServiceNowQuery().in_list("BAD!", ["1"])


class TestSafeToolCall:
    """Tests for the safe_tool_call error-handling wrapper."""

    # Tests are async because safe_tool_call awaits the inner fn coroutine.

    async def test_acl_error_returns_acl_envelope(self) -> None:
        """ACLError is caught and formatted as ACL denial."""
        from servicenow_mcp.errors import ACLError

        async def fn() -> Never:
            raise ACLError("ACL blocked incident")

        result = await safe_tool_call(fn)
        parsed = decode_response(result)
        assert parsed["status"] == "error"
        assert isinstance(parsed["error"], dict)
        assert "Access denied by ServiceNow ACL" in parsed["error"]["message"]
        assert "ACL blocked incident" in parsed["error"]["message"]
        assert "correlation_id" not in parsed

    async def test_get_timeout_returns_query_narrowing_advice(self) -> None:
        async def fn() -> Never:
            raise httpx2.ReadTimeout(
                "private request details",
                request=httpx2.Request(
                    "GET", "https://test.service-now.com/api/now/table/incident"
                ),
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
        "method,timeout_type,phase",
        [
            pytest.param(
                "POST",
                httpx2.ReadTimeout,
                "receiving the response",
                id="POST-ReadTimeout-receiving the response",
            )
        ],
    )
    async def test_change_timeout_requires_verification_before_retry(
        self,
        method: str,
        timeout_type: type[httpx2.TimeoutException],
        phase: str,
    ) -> None:
        async def fn() -> Never:
            raise timeout_type(
                "private request details",
                request=httpx2.Request(
                    method, "https://test.service-now.com/api/now/table/incident"
                ),
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
        async def fn() -> Never:
            raise httpx2.PoolTimeout("private request details")

        message = decode_response(await safe_tool_call(fn))["error"]["message"]

        assert "waiting for a connection" in message
        assert "narrow" not in message.lower()
        assert "private request details" not in message


# ---------------------------------------------------------------------------
# resolve_ref_value
# ---------------------------------------------------------------------------


class TestResolveRefValue:
    """Tests for the resolve_ref_value helper that coerces SN reference fields to strings."""

    def test_integer_returns_str(self) -> None:
        """Integers are coerced via str()."""
        assert resolve_ref_value(42) == "42"


# ---------------------------------------------------------------------------
# validate_identifier - dict coercion
# ---------------------------------------------------------------------------


class TestValidateIdentifierDictCoercion:
    """Tests that validate_identifier defensively coerces dict reference fields."""

    def test_dict_with_valid_display_value(self) -> None:
        """Dict containing a valid identifier in display_value is accepted."""
        ref = {
            "display_value": "sys_user",
            "link": "https://instance.service-now.com/api/...",
        }
        # Should not raise
        validate_identifier(ref)


# ---------------------------------------------------------------------------
# sanitize_query_value - dict coercion
# ---------------------------------------------------------------------------


class TestSanitizeQueryValueDictCoercion:
    """Tests that sanitize_query_value defensively coerces dict reference fields."""

    def test_dict_with_display_value(self) -> None:
        """Dict containing a display_value is resolved before sanitizing."""
        ref = {
            "display_value": "some^value",
            "link": "https://instance.service-now.com/api/...",
        }
        result = sanitize_query_value(ref)
        assert result == "some^^value"
