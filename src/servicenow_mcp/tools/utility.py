"""Utility tools for query building and helper operations."""

import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.state import QueryTokenStore
from servicenow_mcp.utils import ServiceNowQuery, format_response, generate_correlation_id

logger = logging.getLogger(__name__)

# Map of operator names to ServiceNowQuery method signatures
_UNARY_OPERATORS = {"is_empty", "is_not_empty"}
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
}
_OR_BINARY_OPERATORS = {"or_equals", "or_starts_with"}
_LIST_OPERATORS = {"in_list", "not_in_list"}


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register utility tools on the MCP server."""
    query_store: QueryTokenStore = mcp._sn_query_store  # type: ignore[attr-defined]

    @mcp.tool()
    def build_query(conditions: str) -> str:
        """Build a ServiceNow encoded query string from a JSON array of conditions.

        Each condition is an object with:
          - operator: equals, not_equals, greater_than, greater_or_equal,
                      less_than, less_or_equal, contains, starts_with, like,
                      is_empty, is_not_empty, hours_ago, minutes_ago,
                      days_ago, older_than_days, or_equals, or_starts_with,
                      in_list, not_in_list, order_by
          - field: The field name (e.g. "sys_created_on", "active")
          - value: The comparison value (string for most operators, integer for
                   time operators, list of strings for in_list/not_in_list).
                   Not required for is_empty / is_not_empty.
          - descending: (optional, for order_by only) boolean, default false.

        Args:
            conditions: JSON array of condition objects.

        Example:
            [
              {"operator": "equals", "field": "active", "value": "true"},
              {"operator": "hours_ago", "field": "sys_created_on", "value": 24},
              {"operator": "like", "field": "source", "value": "incident"}
            ]
            Returns: "active=true^sys_created_on>=javascript:gs.hoursAgoStart(24)^sourceLIKEincident"

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
                operator = condition.get("operator", "")
                field = condition.get("field", "")
                value = condition.get("value")

                if not operator or not field:
                    return format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error=f"Each condition requires 'operator' and 'field'. Got: {condition}",
                    )

                if operator in _UNARY_OPERATORS:
                    getattr(query, operator)(field)
                elif operator in _TIME_OPERATORS:
                    if value is None:
                        return format_response(
                            data=None,
                            correlation_id=correlation_id,
                            status="error",
                            error=f"Time operator '{operator}' requires an integer 'value'.",
                        )
                    getattr(query, operator)(field, int(value))
                elif operator in _BINARY_OPERATORS or operator in _OR_BINARY_OPERATORS:
                    if value is None:
                        return format_response(
                            data=None,
                            correlation_id=correlation_id,
                            status="error",
                            error=f"Operator '{operator}' requires a 'value'.",
                        )
                    getattr(query, operator)(field, str(value))
                elif operator in _LIST_OPERATORS:
                    if value is None or not isinstance(value, list):
                        return format_response(
                            data=None,
                            correlation_id=correlation_id,
                            status="error",
                            error=f"Operator '{operator}' requires a 'value' that is a list of strings.",
                        )
                    getattr(query, operator)(field, [str(v) for v in value])
                elif operator == "order_by":
                    descending = bool(condition.get("descending", False))
                    query.order_by(field, descending=descending)
                else:
                    valid = sorted(
                        _UNARY_OPERATORS
                        | _TIME_OPERATORS
                        | _BINARY_OPERATORS
                        | _OR_BINARY_OPERATORS
                        | _LIST_OPERATORS
                        | {"order_by"}
                    )
                    return format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error=f"Unknown operator '{operator}'. Valid operators: {valid}",
                    )

            built = query.build()
            query_token = query_store.create({"query": built})
            return format_response(data={"query": built, "query_token": query_token}, correlation_id=correlation_id)

        except json.JSONDecodeError as e:
            return format_response(data=None, correlation_id=correlation_id, status="error", error=f"Invalid JSON: {e}")
        except Exception as e:
            return format_response(data=None, correlation_id=correlation_id, status="error", error=str(e))
