"""Table introspection and query tools."""

import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.mcp_state import get_query_store
from servicenow_mcp.policy import (
    check_table_access,
    enforce_query_safety,
    mask_audit_entry,
    mask_sensitive_fields,
)
from servicenow_mcp.preferred_tools import format_preference_warning, preferred_tool_for
from servicenow_mcp.state import QueryTokenStore
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    resolve_query_token,
    validate_identifier,
)


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# build_query operator constants
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# build_query private helpers
# ---------------------------------------------------------------------------


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
    query: ServiceNowQuery, field: str, operator: str, _condition: dict[str, Any], _correlation_id: str
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
    try:
        int_value = int(value)
    except (ValueError, TypeError):
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"Time operator '{operator}' requires an integer 'value', got: {value!r}",
        )
    getattr(query, operator)(field, int_value)
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
    query: ServiceNowQuery, field: str, _operator: str, condition: dict[str, Any], correlation_id: str
) -> str | None:
    """Apply the between operator (requires start and end)."""
    start = condition.get("start")
    if start is None:
        start = condition.get("value")
    end = condition.get("end")
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
    query: ServiceNowQuery, field: str, _operator: str, condition: dict[str, Any], correlation_id: str
) -> str | None:
    """Apply the datepart operator (requires part, dp_operator, dp_value)."""
    part = condition.get("part", "")
    dp_operator = condition.get("dp_operator")
    if dp_operator is None:
        dp_operator = condition.get("value")
    dp_value = condition.get("dp_value")
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
    query: ServiceNowQuery, field: str, _operator: str, condition: dict[str, Any], correlation_id: str
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

    if not isinstance(operator, str):
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"Condition 'operator' must be a string, got {type(operator).__name__}",
        )

    if operator != "new_query" and not field:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"Operator '{operator}' requires a 'field'. Got: {condition}",
        )

    if operator != "new_query" and not isinstance(field, str):
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"Condition 'field' must be a string, got {type(field).__name__}",
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


def _build_query_impl(
    conditions_list: list[Any],
    query_store: QueryTokenStore,
    correlation_id: str,
) -> str:
    """Process a parsed conditions list and return a formatted TOON response.

    Iterates over each condition dict, applies it to a ``ServiceNowQuery``,
    stores the built query string in *query_store*, and returns the serialized
    response.
    """
    query = ServiceNowQuery()
    for idx, condition in enumerate(conditions_list):
        if not isinstance(condition, dict):
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"conditions[{idx}] must be a JSON object, got {type(condition).__name__}",
            )
        err = _apply_condition(query, condition, correlation_id)
        if err:
            return err

    built = query.build()
    query_token = query_store.create({"query": built})
    return format_response(
        data={"query": built, "query_token": query_token},
        correlation_id=correlation_id,
    )


def _build_field_list(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the field metadata list from sys_dictionary entries and documentation."""
    fields: list[dict[str, Any]] = []
    for col in columns:
        # Start with full original entry, ensure key defaults exist
        field_info = dict(col)  # shallow copy to avoid mutating original
        field_info.setdefault("element", "")
        field_info.setdefault("internal_type", "")
        field_info.setdefault("max_length", "")
        field_info.setdefault("mandatory", "false")
        field_info.setdefault("reference", "")
        field_info.setdefault("column_label", "")
        field_info.setdefault("default_value", "")
        fields.append(field_info)
    return fields


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

TOOL_NAMES: list[str] = [
    "table_describe",
    "table_query",
    "table_aggregate",
    "build_query",
]


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register table introspection and query tools on the MCP server."""
    query_store: QueryTokenStore = get_query_store(mcp)

    @mcp.tool()
    @tool_handler
    async def table_describe(table: str, *, correlation_id: str = "") -> str:
        """Return dictionary metadata for a table: fields, types, references, choices and attributes.

        Args:
            table: The ServiceNow table name (e.g., 'incident', 'sys_user').
        """
        validate_identifier(table)
        check_table_access(table)
        async with ServiceNowClient(settings, auth_provider) as client:
            metadata = await client.get_metadata(table)

            # Fetch table-level metadata from sys_db_object
            table_meta = await client.query_records(
                "sys_db_object",
                ServiceNowQuery().equals("name", table).build(),
                fields=["label", "super_class", "is_extendable", "number_ref", "sys_id"],
                limit=1,
            )
            table_info = table_meta.get("records", [{}])[0] if table_meta.get("records") else {}

            # Fetch field documentation from sys_documentation
            docs_result = await client.query_records(
                "sys_documentation",
                ServiceNowQuery().equals("name", table).build(),
                fields=["element", "label", "help", "hint", "url"],
                limit=500,
            )
            docs = {d["element"]: d for d in docs_result.get("records", []) if d.get("element")}

            doc_warnings: list[str] = []
            if len(docs_result.get("records", [])) >= 500:
                doc_warnings.append("Documentation records may be truncated at 500 entries")

        fields = _build_field_list(metadata)

        return format_response(
            data={
                "table": table_info,
                "fields": fields,
                "field_count": len(fields),
                "documentation": docs,
            },
            correlation_id=correlation_id,
            warnings=doc_warnings or None,
        )

    @mcp.tool()
    @tool_handler
    async def table_query(
        table: str,
        query_token: str = "",
        fields: str = "",
        limit: int = 100,
        offset: int = 0,
        order_by: str = "",
        display_values: bool = False,
        *,
        correlation_id: str = "",
    ) -> str:
        """Generic record query - fallback when no specialized tool fits.

        Prefer a specialized tool first when one exists for the target table.
        Specialized tools resolve human-readable choice labels (e.g. state="open"),
        apply sensitivity masking, return display values by default, and use
        sensible field defaults. ``table_query`` does none of these automatically.

        Decision tree:

        - Need a record by INC / CHG / PRB / REQ / RITM / KB number?
          Use ``incident_get`` / ``change_get`` / ``problem_get`` / ``request_get``
          / ``request_item_get`` / ``knowledge_get``.
        - Listing by common business filters (state, priority, assignment_group)?
          Use ``incident_list`` / ``change_list`` / ``problem_list`` / ``request_list``
          / ``cmdb_list`` / ``knowledge_search``.
        - Audit / history / "who changed what" / timeline?
          Use ``changes_last_touched`` or ``debug_trace``.
        - Listing or fetching scripts (business rules, script includes, UI actions, etc.)?
          Use ``meta_list_artifacts`` / ``meta_get_artifact``.
        - Attachments?
          Use ``attachment_list`` / ``attachment_get`` / ``attachment_download``.
        - Schema / dictionary inspection?
          Use ``table_describe``.
        - CMDB relationships?
          Use ``cmdb_relationships``.

        Use ``table_query`` only when none of the above fits or when you need
        an exotic filter not exposed by a specialized tool. When you do call
        ``table_query`` on a table that has a specialized alternative, a
        steering warning is added to the response so the agent can self-correct
        on the next call.

        Args:
            table: The ServiceNow table name.
            query_token: Token from the build_query tool representing a ServiceNow encoded query.
                Use build_query to create a query first, then pass the returned query_token here.
                Leave empty for no filter.
            fields: Comma-separated list of fields to return (empty for all).
            limit: Maximum number of records to return (capped by policy).
            offset: Number of records to skip for pagination.
            order_by: Field to sort results by (empty for default).
            display_values: If True, return display values instead of raw values.
        """
        warnings: list[str] = []

        query = resolve_query_token(query_token, query_store, correlation_id)
        validate_identifier(table)
        check_table_access(table)
        safety = enforce_query_safety(table, query, limit, settings)
        effective_limit = safety["limit"]
        if effective_limit < limit:
            warnings.append(f"Limit capped at {effective_limit}")

        preferred = preferred_tool_for(table)
        if preferred is not None:
            warnings.append(format_preference_warning(table, preferred))

        field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None
        order = order_by or None

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table,
                query,
                fields=field_list,
                limit=effective_limit,
                offset=offset,
                order_by=order,
                display_values=display_values,
            )

        # Mask sensitive fields in each record
        if table == "sys_audit":
            masked_records = [mask_audit_entry(r) for r in result["records"]]
        else:
            masked_records = [mask_sensitive_fields(r) for r in result["records"]]

        return format_response(
            data=masked_records,
            correlation_id=correlation_id,
            pagination={
                "offset": offset,
                "limit": effective_limit,
                "total": result["count"],
            },
            warnings=warnings or None,
        )

    @mcp.tool()
    @tool_handler
    async def table_aggregate(
        table: str,
        query_token: str = "",
        group_by: str = "",
        avg_fields: str = "",
        min_fields: str = "",
        max_fields: str = "",
        sum_fields: str = "",
        *,
        correlation_id: str = "",
    ) -> str:
        """Compute aggregate statistics for a table (counts, min, max, avg, sum).

        Count is always included. For field-specific stats, provide comma-separated
        field names (e.g. avg_fields="priority,impact").

        Args:
            table: The ServiceNow table name.
            query_token: Token from the build_query tool representing a ServiceNow encoded query.
                Use build_query to create a query first, then pass the returned query_token here.
                Leave empty for no filter.
            group_by: Field to group results by (empty for no grouping).
            avg_fields: Comma-separated fields to compute average for.
            min_fields: Comma-separated fields to compute minimum for.
            max_fields: Comma-separated fields to compute maximum for.
            sum_fields: Comma-separated fields to compute sum for.
        """
        query = resolve_query_token(query_token, query_store, correlation_id)
        validate_identifier(table)
        check_table_access(table)
        enforce_query_safety(table, query, None, settings)
        group = group_by or None

        def _split(s: str) -> list[str] | None:
            parts = [p.strip() for p in s.split(",") if p.strip()]
            return parts or None

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.aggregate(
                table,
                query,
                group_by=group,
                avg_fields=_split(avg_fields),
                min_fields=_split(min_fields),
                max_fields=_split(max_fields),
                sum_fields=_split(sum_fields),
            )

        return format_response(data=result, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def build_query(conditions: str, correlation_id: str = "") -> str:
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
        try:
            parsed = json.loads(conditions)
        except json.JSONDecodeError as e:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"Invalid JSON: {e}",
            )

        if not isinstance(parsed, list):
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error="conditions must be a JSON array",
            )

        return _build_query_impl(parsed, query_store, correlation_id)
