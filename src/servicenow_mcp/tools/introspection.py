"""Introspection tools for ServiceNow table discovery and querying."""

import json

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import (
    check_table_access,
    enforce_query_safety,
    mask_sensitive_fields,
)
from servicenow_mcp.utils import format_response, generate_correlation_id, validate_identifier


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register introspection tools on the MCP server."""

    @mcp.tool()
    async def table_describe(table: str) -> str:
        """Return dictionary metadata for a table: fields, types, references, choices and attributes.

        Args:
            table: The ServiceNow table name (e.g., 'incident', 'sys_user').
        """
        correlation_id = generate_correlation_id()
        try:
            validate_identifier(table)
            check_table_access(table)
            async with ServiceNowClient(settings, auth_provider) as client:
                metadata = await client.get_metadata(table)
            fields = []
            for entry in metadata:
                field_info = {
                    "element": entry.get("element", ""),
                    "internal_type": entry.get("internal_type", ""),
                    "max_length": entry.get("max_length", ""),
                    "mandatory": entry.get("mandatory", "false"),
                    "reference": entry.get("reference", ""),
                    "column_label": entry.get("column_label", ""),
                    "default_value": entry.get("default_value", ""),
                }
                fields.append(field_info)
            return json.dumps(
                format_response(
                    data={"table": table, "fields": fields, "field_count": len(fields)},
                    correlation_id=correlation_id,
                ),
                indent=2,
            )
        except Exception as e:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(e),
                ),
                indent=2,
            )

    @mcp.tool()
    async def table_get(
        table: str,
        sys_id: str,
        fields: str = "",
        display_values: bool = False,
    ) -> str:
        """Fetch a single record by sys_id with optional field selection.

        Args:
            table: The ServiceNow table name.
            sys_id: The sys_id of the record to fetch.
            fields: Comma-separated list of fields to return (empty for all).
            display_values: If True, return display values instead of raw values.
        """
        correlation_id = generate_correlation_id()
        try:
            validate_identifier(table)
            check_table_access(table)
            field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None
            async with ServiceNowClient(settings, auth_provider) as client:
                record = await client.get_record(table, sys_id, fields=field_list, display_values=display_values)
            record = mask_sensitive_fields(record)
            return json.dumps(
                format_response(data=record, correlation_id=correlation_id),
                indent=2,
            )
        except Exception as e:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(e),
                ),
                indent=2,
            )

    @mcp.tool()
    async def table_query(
        table: str,
        query: str,
        fields: str = "",
        limit: int = 100,
        offset: int = 0,
        order_by: str = "",
    ) -> str:
        """Query any table with filter conditions, returning matching records.

        Args:
            table: The ServiceNow table name.
            query: ServiceNow encoded query string (e.g., 'active=true^priority=1').
            fields: Comma-separated list of fields to return (empty for all).
            limit: Maximum number of records to return (capped by policy).
            offset: Number of records to skip for pagination.
            order_by: Field to sort results by (empty for default).
        """
        correlation_id = generate_correlation_id()
        warnings: list[str] = []
        try:
            validate_identifier(table)
            check_table_access(table)
            safety = enforce_query_safety(table, query, limit, settings)
            effective_limit = safety["limit"]
            if effective_limit < limit:
                warnings.append(f"Limit capped at {effective_limit}")

            field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None
            order = order_by if order_by else None

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table,
                    query,
                    fields=field_list,
                    limit=effective_limit,
                    offset=offset,
                    order_by=order,
                )

            # Mask sensitive fields in each record
            masked_records = [mask_sensitive_fields(r) for r in result["records"]]

            return json.dumps(
                format_response(
                    data=masked_records,
                    correlation_id=correlation_id,
                    pagination={
                        "offset": offset,
                        "limit": effective_limit,
                        "total": result["count"],
                    },
                    warnings=warnings if warnings else None,
                ),
                indent=2,
            )
        except Exception as e:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(e),
                ),
                indent=2,
            )

    @mcp.tool()
    async def table_aggregate(
        table: str,
        query: str,
        group_by: str = "",
        avg_fields: str = "",
        min_fields: str = "",
        max_fields: str = "",
        sum_fields: str = "",
    ) -> str:
        """Compute aggregate statistics for a table (counts, min, max, avg, sum).

        Count is always included. For field-specific stats, provide comma-separated
        field names (e.g. avg_fields="priority,impact").

        Args:
            table: The ServiceNow table name.
            query: ServiceNow encoded query string for filtering.
            group_by: Field to group results by (empty for no grouping).
            avg_fields: Comma-separated fields to compute average for.
            min_fields: Comma-separated fields to compute minimum for.
            max_fields: Comma-separated fields to compute maximum for.
            sum_fields: Comma-separated fields to compute sum for.
        """
        correlation_id = generate_correlation_id()
        try:
            validate_identifier(table)
            check_table_access(table)
            enforce_query_safety(table, query, None, settings)
            group = group_by if group_by else None

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

            return json.dumps(
                format_response(data=result, correlation_id=correlation_id),
                indent=2,
            )
        except Exception as e:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(e),
                ),
                indent=2,
            )
