"""Record-level read operations for ServiceNow tables."""

import asyncio
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    INTERNAL_QUERY_LIMIT,
    check_table_access,
    is_table_denied,
    mask_sensitive_fields,
)
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    validate_identifier,
)


logger = logging.getLogger(__name__)


async def _paginate_dictionary_entries(
    client: ServiceNowClient,
    query_str: str,
    page_size: int = INTERNAL_QUERY_LIMIT,
) -> list[dict[str, Any]]:
    """Paginate through all sys_dictionary entries matching *query_str*."""
    all_records: list[dict[str, Any]] = []
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
        all_records.extend(records)
        if len(records) < page_size:
            break
        offset += page_size

    return all_records


def _filter_reference_fields(records: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Filter dictionary records to valid reference fields, excluding denied/system tables.

    Returns a list of ``(table_name, field_name)`` tuples.
    """
    filtered: list[tuple[str, str]] = []
    for field in records:
        ref_table = field.get("name", "")
        ref_field = field.get("element", "")
        if not ref_table or not ref_field:
            continue
        if is_table_denied(ref_table):
            continue
        if ref_table.startswith(("var__m_", "sys_variable_value")):
            continue
        filtered.append((ref_table, ref_field))
    return filtered


async def _lookup_single_reference(
    client: ServiceNowClient,
    sem: asyncio.Semaphore,
    ref_table: str,
    ref_field: str,
    sys_id: str,
) -> dict[str, Any] | None:
    """Look up records referencing *sys_id* via a single reference field."""
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
            logger.debug("Reference lookup failed for %s.%s -> %s", ref_table, ref_field, sys_id, exc_info=True)
    return None


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


TOOL_NAMES: list[str] = [
    "record_get",
    "rel_references_to",
    "rel_references_from",
]


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register record read operation tools on the MCP server."""

    # ------------------------------------------------------------------
    # Read tools
    # ------------------------------------------------------------------

    @mcp.tool()
    @tool_handler
    async def record_get(
        table: str,
        sys_id: str,
        fields: str = "",
        display_values: bool = False,
        include_script_body: bool = False,
        *,
        correlation_id: str = "",
    ) -> str:
        """Fetch a single record by sys_id with optional field selection.

        Args:
            table: The ServiceNow table name.
            sys_id: The sys_id of the record to fetch.
            fields: Comma-separated list of fields to return (empty for all).
            display_values: If True, return display values instead of raw values.
            include_script_body: If True, return script/markup body fields
                verbatim. Script/markup bodies are masked by default. Set True
                only when you need to inspect the code itself; script bodies
                may contain hardcoded secrets.
        """
        validate_identifier(table)
        check_table_access(table)
        field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None
        async with ServiceNowClient(settings, auth_provider) as client:
            record = await client.get_record(table, sys_id, fields=field_list, display_values=display_values)
        record = mask_sensitive_fields(record, table=table, include_script_body=include_script_body)
        return format_response(data=record, correlation_id=correlation_id)

    # ------------------------------------------------------------------
    # Relationship tools
    # ------------------------------------------------------------------

    @mcp.tool()
    @tool_handler
    async def rel_references_to(table: str, sys_id: str, *, correlation_id: str = "") -> str:
        """Find records in other tables that reference a given record.

        Args:
            table: The table of the target record.
            sys_id: The sys_id of the target record.
        """
        validate_identifier(table)
        check_table_access(table)
        async with ServiceNowClient(settings, auth_provider) as client:
            query_str = ServiceNowQuery().equals("internal_type", "reference").equals("reference", table).build()
            all_ref_records = await _paginate_dictionary_entries(client, query_str)
            filtered_refs = _filter_reference_fields(all_ref_records)

            sem = asyncio.Semaphore(10)
            tasks = [
                _lookup_single_reference(client, sem, ref_table, ref_field, sys_id)
                for ref_table, ref_field in filtered_refs
            ]
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
    async def rel_references_from(
        table: str,
        sys_id: str,
        include_script_body: bool = False,
        *,
        correlation_id: str = "",
    ) -> str:
        """Find what a record references by inspecting its reference fields.

        Args:
            table: The table of the source record.
            sys_id: The sys_id of the source record.
            include_script_body: If True, return script/markup body fields
                verbatim. Script/markup bodies are masked by default. Set True
                only when you need to inspect the code itself; script bodies
                may contain hardcoded secrets.
        """
        validate_identifier(table)
        check_table_access(table)
        async with ServiceNowClient(settings, auth_provider) as client:
            # Get the record
            record = mask_sensitive_fields(
                await client.get_record(table, sys_id, display_values=True),
                table=table,
                include_script_body=include_script_body,
            )

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
