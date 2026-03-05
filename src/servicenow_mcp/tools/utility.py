"""Utility tools for query building and helper operations."""

import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.state import QueryTokenStore
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    generate_correlation_id,
)


logger = logging.getLogger(__name__)

# Map of operator names to ServiceNowQuery method signatures
_UNARY_OPERATORS = {
    "is_empty",
    "is_not_empty",
    "anything",
    "empty_string",
    "val_changes",
}
_TIME_OPERATORS = {"hours_ago", "minutes_ago", "days_ago", "older_than_days"}
_BINARY_OPERATORS = {
    "equals",
    "not_equals",
    "greater_than",
    "greater_or_equal",
    "less_than",
    "less_or_equal",
    "contains",
    "starts_with",
    "like",
    "ends_with",
    "not_like",
    "does_not_contain",
    "on",
    "not_on",
    "relative_gt",
    "relative_lt",
    "more_than",
    "changes_from",
    "changes_to",
    "dynamic",
    "in_hierarchy",
}
_OR_BINARY_OPERATORS = {"or_equals", "or_starts_with"}
_LIST_OPERATORS = {"in_list", "not_in_list"}
_FIELD_OPERATORS = {
    "gt_field",
    "lt_field",
    "gt_or_equals_field",
    "lt_or_equals_field",
    "same_as",
    "not_same_as",
}

_ALL_VALID_OPERATORS = sorted(
    _UNARY_OPERATORS
    | _TIME_OPERATORS
    | _BINARY_OPERATORS
    | _OR_BINARY_OPERATORS
    | _LIST_OPERATORS
    | _FIELD_OPERATORS
    | {"order_by", "between", "datepart", "new_query", "rl_query"}
)


def _require_value(
    condition: dict[str, Any],
    operator: str,
    correlation_id: str,
    message: str | None = None,
) -> tuple[Any, str | None]:
    """Return (value, None) if condition has a non-None 'value', else (None, error_response)."""
    value = condition.get("value")
    if value is None:
        msg = message or f"Operator '{operator}' requires a 'value'."
        return None, format_response(data=None, correlation_id=correlation_id, status="error", error=msg)
    return value, None


def _apply_unary(
    query: ServiceNowQuery, field: str, operator: str, condition: dict[str, Any], correlation_id: str
) -> str | None:
    """Apply a unary operator (no value needed)."""
    getattr(query, operator)(field)
    return None


def _apply_time(
    query: ServiceNowQuery, field: str, operator: str, condition: dict[str, Any], correlation_id: str
) -> str | None:
    """Apply a time operator (requires integer value)."""
    value, err = _require_value(
        condition, operator, correlation_id, f"Time operator '{operator}' requires an integer 'value'."
    )
    if err:
        return err
    getattr(query, operator)(field, int(value))
    return None


def _apply_binary(
    query: ServiceNowQuery, field: str, operator: str, condition: dict[str, Any], correlation_id: str
) -> str | None:
    """Apply a binary or OR-binary operator (requires string value)."""
    value, err = _require_value(condition, operator, correlation_id)
    if err:
        return err
    getattr(query, operator)(field, str(value))
    return None


def _apply_list(
    query: ServiceNowQuery, field: str, operator: str, condition: dict[str, Any], correlation_id: str
) -> str | None:
    """Apply a list operator (requires list value)."""
    value = condition.get("value")
    if value is None or not isinstance(value, list):
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"Operator '{operator}' requires a 'value' that is a list of strings.",
        )
    getattr(query, operator)(field, [str(v) for v in value])
    return None


def _apply_field(
    query: ServiceNowQuery, field: str, operator: str, condition: dict[str, Any], correlation_id: str
) -> str | None:
    """Apply a field comparison operator (requires other_field)."""
    other_field = condition.get("other_field") or condition.get("value")
    if not other_field:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"Operator '{operator}' requires 'other_field' (or 'value' as the other field name).",
        )
    getattr(query, operator)(field, str(other_field))
    return None


def _apply_between(
    query: ServiceNowQuery, field: str, operator: str, condition: dict[str, Any], correlation_id: str
) -> str | None:
    """Apply the between operator (requires start and end)."""
    start = condition.get("start") or condition.get("value")
    end = condition.get("end", "")
    if start is None or start == "" or end is None or end == "":
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error="Operator 'between' requires 'start' and 'end' values (or 'value' for start).",
        )
    query.between(field, str(start), str(end))
    return None


def _apply_datepart(
    query: ServiceNowQuery, field: str, operator: str, condition: dict[str, Any], correlation_id: str
) -> str | None:
    """Apply the datepart operator (requires part, dp_operator, dp_value)."""
    part = condition.get("part", "")
    dp_operator = condition.get("dp_operator") or condition.get("value", "")
    dp_value = condition.get("dp_value", "")
    if part is None or part == "" or dp_operator is None or dp_operator == "" or dp_value is None or dp_value == "":
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error="Operator 'datepart' requires 'part', 'dp_operator', and 'dp_value'.",
        )
    query.datepart(field, str(part), str(dp_operator), str(dp_value))
    return None


def _apply_new_query(
    query: ServiceNowQuery, _field: str, _operator: str, _condition: dict[str, Any], _correlation_id: str
) -> str | None:
    """Apply the new_query separator."""
    query.new_query()
    return None


def _apply_rl_query(
    query: ServiceNowQuery, field: str, operator: str, condition: dict[str, Any], correlation_id: str
) -> str | None:
    """Apply the rl_query operator (requires related_table, related_field, rl_operator)."""
    related_table = condition.get("related_table", "")
    related_field = condition.get("related_field") or field
    rl_operator = condition.get("rl_operator", "")
    rl_value = condition.get("value", "")
    if not related_table or not related_field or not rl_operator:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error="Operator 'rl_query' requires 'related_table', 'related_field' (or 'field'), and 'rl_operator'.",
        )
    query.rl_query(str(related_table), str(related_field), str(rl_operator), str(rl_value))
    return None


def _apply_order_by(
    query: ServiceNowQuery, field: str, _operator: str, condition: dict[str, Any], _correlation_id: str
) -> str | None:
    """Apply the order_by operator."""
    descending = bool(condition.get("descending", False))
    query.order_by(field, descending=descending)
    return None


def _get_handler(operator: str) -> Any | None:
    """Look up the handler function for an operator, or None if unknown."""
    if operator in _UNARY_OPERATORS:
        return _apply_unary
    if operator in _TIME_OPERATORS:
        return _apply_time
    if operator in _BINARY_OPERATORS or operator in _OR_BINARY_OPERATORS:
        return _apply_binary
    if operator in _LIST_OPERATORS:
        return _apply_list
    if operator in _FIELD_OPERATORS:
        return _apply_field
    # Single-operator handlers
    return {
        "between": _apply_between,
        "datepart": _apply_datepart,
        "new_query": _apply_new_query,
        "rl_query": _apply_rl_query,
        "order_by": _apply_order_by,
    }.get(operator)


def _apply_condition(
    query: ServiceNowQuery,
    condition: dict[str, Any],
    correlation_id: str,
) -> str | None:
    """Process a single condition object and apply it to the query.

    Returns None on success, or a formatted error response string on failure.
    """
    operator = condition.get("operator", "")
    field = condition.get("field", "")

    if not operator:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"Each condition requires 'operator'. Got: {condition}",
        )

    if operator != "new_query" and not field:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"Operator '{operator}' requires a 'field'. Got: {condition}",
        )

    handler = _get_handler(operator)
    if handler is None:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"Unknown operator '{operator}'. Valid operators: {_ALL_VALID_OPERATORS}",
        )

    return handler(query, field, operator, condition, correlation_id)


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register utility tools on the MCP server."""
    _ = settings, auth_provider  # Required by register_tools interface contract
    query_store: QueryTokenStore = mcp._sn_query_store  # type: ignore[attr-defined]

    @mcp.tool()
    def build_query(conditions: str) -> str:
        """Build a ServiceNow encoded query string from a JSON array of conditions.

        Each condition is an object with:
          - operator: The query operator (see groups below).
          - field: The field name (not required for ``new_query``).
          - value: The comparison value (type depends on operator).

        Operator groups:
          **Comparison:** equals, not_equals, greater_than, greater_or_equal,
            less_than, less_or_equal
          **String:** contains, starts_with, like, ends_with, not_like,
            does_not_contain
          **Null / special:** is_empty, is_not_empty, anything, empty_string
          **Time:** hours_ago, minutes_ago, days_ago, older_than_days
          **Date:** on, not_on, relative_gt, relative_lt, more_than
          **Date part:** datepart (requires ``part``, ``dp_operator``, ``dp_value``)
          **Range:** between (requires ``start``, ``end``)
          **Field comparison:** gt_field, lt_field, gt_or_equals_field,
            lt_or_equals_field, same_as, not_same_as (use ``other_field`` for
            the second field name)
          **Reference:** dynamic, in_hierarchy
          **Change detection:** val_changes, changes_from, changes_to
          **Logical:** new_query (no field needed - inserts ^NQ separator)
          **Related list:** rl_query (requires ``related_table``, ``related_field``,
            ``rl_operator``, ``value``)
          **List:** in_list, not_in_list (value is a list of strings)
          **OR:** or_equals, or_starts_with
          **Ordering:** order_by (optional ``descending``: boolean)

        Args:
            conditions: JSON array of condition objects.

        Returns a response containing both the built query string and a query_token.
        The query_token must be passed to other tools that accept query parameters.
        """
        correlation_id = generate_correlation_id()
        try:
            parsed: list[dict[str, Any]] = json.loads(conditions)
            if not isinstance(parsed, list):
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error="conditions must be a JSON array",
                )

            query = ServiceNowQuery()
            for condition in parsed:
                err = _apply_condition(query, condition, correlation_id)
                if err:
                    return err

            built = query.build()
            query_token = query_store.create({"query": built})
            return format_response(
                data={"query": built, "query_token": query_token},
                correlation_id=correlation_id,
            )

        except json.JSONDecodeError as e:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"Invalid JSON: {e}",
            )
        except Exception as e:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=str(e),
            )
