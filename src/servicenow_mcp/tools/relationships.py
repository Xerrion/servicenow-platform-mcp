"""Relationship tools for traversing ServiceNow reference fields."""

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    DENIED_TABLES,
    INTERNAL_QUERY_LIMIT,
    check_table_access,
    mask_sensitive_fields,
)
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    validate_identifier,
)


async def _resolve_table_hierarchy(client: ServiceNowClient, table: str) -> list[str]:
    """Resolve a table's inheritance chain by walking sys_db_object.super_class.

    Returns a list starting with *table* followed by its ancestors (e.g.
    ``["incident", "task"]``).  The walk is capped at 10 iterations to
    guard against circular references.
    """
    tables = [table]
    current_table = table

    for _ in range(10):
        result = await client.query_records(
            "sys_db_object",
            ServiceNowQuery().equals("name", current_table).build(),
            fields=["super_class"],
            limit=1,
        )
        records = result.get("records", [])
        if not records:
            break

        super_class = records[0].get("super_class", "")
        if not super_class:
            break

        # super_class is a reference field -- may come back as a dict or plain string
        super_class_id = super_class.get("value", "") if isinstance(super_class, dict) else super_class

        if not super_class_id:
            break

        # Resolve the sys_id to the parent table's name
        parent = await client.get_record("sys_db_object", super_class_id, fields=["name"])
        parent_name = parent.get("name", "")
        if not parent_name or parent_name in tables:
            break

        tables.append(parent_name)
        current_table = parent_name

    return tables


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register relationship tools on the MCP server."""

    @mcp.tool()
    @tool_handler
    async def rel_references_to(table: str, sys_id: str, *, correlation_id: str) -> str:
        """Find records in other tables that reference a given record.

        Args:
            table: The table of the target record.
            sys_id: The sys_id of the target record.
        """
        validate_identifier(table)
        check_table_access(table)
        # Query sys_dictionary for reference fields pointing to this table
        async with ServiceNowClient(settings, auth_provider) as client:
            query_str = ServiceNowQuery().equals("internal_type", "reference").equals("reference", table).build()

            # Paginate through ALL dictionary entries that reference the target table
            all_ref_records: list[dict[str, Any]] = []
            page_size = INTERNAL_QUERY_LIMIT
            offset = 0

            while True:
                page = await client.query_records(
                    "sys_dictionary",
                    query_str,
                    fields=["name", "element", "reference", "column_label"],
                    limit=page_size,
                    offset=offset,
                )
                records = page.get("records", [])
                all_ref_records.extend(records)
                if len(records) < page_size:
                    break
                offset += page_size

            # Filter out denied tables and system-internal entries
            filtered_refs: list[tuple[str, str]] = []
            for field in all_ref_records:
                ref_table = field.get("name", "")
                ref_field = field.get("element", "")
                if not ref_table or not ref_field:
                    continue
                if ref_table.lower() in DENIED_TABLES:
                    continue
                if ref_table.startswith(("var__m_", "sys_variable_value")):
                    continue
                filtered_refs.append((ref_table, ref_field))

            # Build lookup tasks for each valid reference field
            sem = asyncio.Semaphore(10)

            async def _lookup_ref(ref_table: str, ref_field: str) -> dict[str, Any] | None:
                """Look up records referencing the target via a single reference field."""
                async with sem:
                    try:
                        check_table_access(ref_table)
                        ref_records = await client.query_records(
                            ref_table,
                            ServiceNowQuery().equals(ref_field, sys_id).build(),
                            fields=["sys_id", ref_field],
                            limit=10,
                        )
                        if ref_records["records"]:
                            masked_records = [mask_sensitive_fields(r) for r in ref_records["records"][:5]]
                            return {
                                "table": ref_table,
                                "field": ref_field,
                                "count": ref_records["count"],
                                "sample_records": masked_records,
                            }
                    except Exception:
                        pass
                return None

            tasks = [_lookup_ref(ref_table, ref_field) for ref_table, ref_field in filtered_refs]

            results = await asyncio.gather(*tasks)
            references = [r for r in results if r is not None]

        return format_response(
            data={
                "target": {"table": table, "sys_id": sys_id},
                "incoming_references": references,
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def rel_references_from(table: str, sys_id: str, *, correlation_id: str) -> str:
        """Find what a record references by inspecting its reference fields.

        Args:
            table: The table of the source record.
            sys_id: The sys_id of the source record.
        """
        validate_identifier(table)
        check_table_access(table)
        async with ServiceNowClient(settings, auth_provider) as client:
            # Get the record
            record = mask_sensitive_fields(await client.get_record(table, sys_id, display_values=True))

            # Resolve full table hierarchy (e.g. incident -> task) so we
            # pick up inherited reference fields from parent tables.
            table_hierarchy = await _resolve_table_hierarchy(client, table)

            ref_fields = await client.query_records(
                "sys_dictionary",
                ServiceNowQuery().in_list("name", table_hierarchy).equals("internal_type", "reference").build(),
                fields=["element", "reference", "column_label"],
                limit=INTERNAL_QUERY_LIMIT,
            )

            outgoing: list[dict[str, Any]] = []
            for field in ref_fields["records"]:
                field_name = field.get("element", "")
                ref_table = field.get("reference", "")
                if field_name and field_name in record and record[field_name]:
                    outgoing.append(
                        {
                            "field": field_name,
                            "reference_table": ref_table,
                            "value": record[field_name],
                            "label": field.get("column_label", ""),
                        }
                    )

        return format_response(
            data={
                "source": {"table": table, "sys_id": sys_id},
                "outgoing_references": outgoing,
            },
            correlation_id=correlation_id,
        )
