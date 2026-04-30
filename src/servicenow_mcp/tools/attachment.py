"""Attachment read tools for ServiceNow attachments."""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.errors import NotFoundError, PolicyError
from servicenow_mcp.policy import check_table_access, enforce_query_safety, mask_sensitive_fields
from servicenow_mcp.tools._attachment_common import (
    build_attachment_download_payload,
    ensure_attachment_size_value_within_limit,
    ensure_attachment_size_within_limit,
    get_attachment_size_bytes,
    get_attachment_sys_id,
    get_attachment_table_name,
)
from servicenow_mcp.utils import ServiceNowQuery, format_response, validate_identifier, validate_sys_id


logger = logging.getLogger(__name__)


TOOL_NAMES: list[str] = [
    "attachment_list",
    "attachment_get",
    "attachment_download",
    "attachment_download_by_name",
]


def _build_attachment_query(table_name: str, table_sys_id: str, file_name: str) -> str:
    """Build a sys_attachment metadata query from validated filters."""
    query = ServiceNowQuery()
    if table_name:
        query.equals("table_name", table_name)
    if table_sys_id:
        query.equals("table_sys_id", table_sys_id)
    if file_name:
        query.equals("file_name", file_name)
    return query.build()


def _validate_attachment_list_inputs(table_name: str, table_sys_id: str, order_by: str) -> None:
    """Validate attachment list filters before querying ServiceNow."""
    if table_name:
        validate_identifier(table_name)
        check_table_access(table_name)
    if table_sys_id:
        validate_sys_id(table_sys_id)
    if order_by:
        validate_identifier(order_by)


def _append_attachment_order_by(query: str, order_by: str) -> str:
    """Append an order-by clause to an attachment query when requested."""
    if not order_by:
        return query

    order_query = ServiceNowQuery().order_by(order_by).build()
    return f"{query}^{order_query}" if query else order_query


def _filter_and_mask_attachment_records(
    records: list[dict[str, Any]],
    *,
    table_name: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Return visible attachment records and whether any were omitted by policy."""
    if table_name:
        return [mask_sensitive_fields(record) for record in records], False

    visible_records: list[dict[str, Any]] = []
    blocked_tables: set[str] = set()
    for record in records:
        record_table_name = get_attachment_table_name(record)
        if record_table_name in blocked_tables:
            continue
        try:
            check_table_access(record_table_name)
        except PolicyError:
            blocked_tables.add(record_table_name)
            continue
        visible_records.append(mask_sensitive_fields(record))

    return visible_records, bool(blocked_tables)


def _build_attachment_list_metadata(
    *,
    requested_limit: int,
    effective_limit: int,
    offset: int,
    visible_total: int,
    omitted_by_policy: bool,
) -> tuple[dict[str, int], list[str] | None]:
    """Build stable pagination and warnings for attachment list responses."""
    warnings: list[str] = []
    if effective_limit < requested_limit:
        warnings.append(f"Limit capped at {effective_limit}")
    if omitted_by_policy:
        warnings.append("Some attachments were omitted due to table access policy")

    pagination = {
        "offset": offset,
        "limit": effective_limit,
        "total": visible_total,
    }
    return pagination, warnings or None


def _require_bytes_content(content: object) -> bytes:
    """Return attachment content only when the client result is binary."""
    if not isinstance(content, bytes):
        raise TypeError("Attachment download content must be bytes")
    return content


async def _get_attachment_metadata_checked(client: ServiceNowClient, sys_id: str) -> dict[str, Any]:
    """Fetch attachment metadata and enforce real-table read access."""
    metadata = await client.get_attachment(sys_id)
    check_table_access(get_attachment_table_name(metadata))
    return metadata


async def _get_attachment_metadata_by_name_checked(
    client: ServiceNowClient,
    table_name: str,
    table_sys_id: str,
    file_name: str,
) -> tuple[dict[str, Any], str, list[str] | None]:
    """Resolve attachment metadata by logical identity before downloading content."""
    query = _build_attachment_query(table_name, table_sys_id, file_name)
    query = _append_attachment_order_by(query, "sys_created_on")
    result = await client.query_records(
        "sys_attachment",
        query,
        fields=["sys_id", "table_name", "table_sys_id", "file_name", "content_type", "size_bytes"],
        limit=2,
    )
    records = result["records"]
    if not records:
        raise NotFoundError(
            f"Attachment '{file_name}' was not found for table '{table_name}' and record '{table_sys_id}'"
        )

    metadata = records[0]
    attachment_sys_id = get_attachment_sys_id(metadata)
    check_table_access(get_attachment_table_name(metadata))

    if len(records) == 1:
        return metadata, attachment_sys_id, None
    return metadata, attachment_sys_id, ["Multiple attachments matched; returned the earliest created attachment"]


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register attachment read tools on the MCP server."""

    @mcp.tool()
    @tool_handler
    async def attachment_list(
        table_name: str = "",
        table_sys_id: str = "",
        file_name: str = "",
        limit: int = 20,
        offset: int = 0,
        order_by: str = "sys_created_on",
        *,
        correlation_id: str = "",
    ) -> str:
        """List attachment metadata records with optional filters.

        Preferred over `table_query` on `sys_attachment` - applies attachment-table-access policy filtering and exposes the standard `(table_name, table_sys_id, file_name)` filter shape.

        Args:
            table_name: Optional table name to filter attachments by.
            table_sys_id: Optional source record sys_id to filter attachments by.
            file_name: Optional attachment file name to filter by.
            limit: Maximum number of attachments to return.
            offset: Number of matching attachments to skip.
            order_by: Field to sort by.
        """
        _validate_attachment_list_inputs(table_name, table_sys_id, order_by)

        query = _append_attachment_order_by(_build_attachment_query(table_name, table_sys_id, file_name), order_by)
        safety = enforce_query_safety("sys_attachment", query, limit, settings)
        effective_limit = safety["limit"]

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.list_attachments(
                query,
                effective_limit,
                offset,
            )

        masked_records, omitted_by_policy = _filter_and_mask_attachment_records(
            result["records"], table_name=table_name
        )
        pagination, warnings = _build_attachment_list_metadata(
            requested_limit=limit,
            effective_limit=effective_limit,
            offset=offset,
            visible_total=len(masked_records),
            omitted_by_policy=omitted_by_policy,
        )
        return format_response(
            data=masked_records,
            correlation_id=correlation_id,
            pagination=pagination,
            warnings=warnings,
        )

    @mcp.tool()
    @tool_handler
    async def attachment_get(sys_id: str, *, correlation_id: str = "") -> str:
        """Fetch attachment metadata by attachment sys_id.

        Preferred over `record_get` on `sys_attachment` - applies attachment-table-access policy.

        Args:
            sys_id: The sys_id of the attachment.
        """
        validate_sys_id(sys_id)
        async with ServiceNowClient(settings, auth_provider) as client:
            metadata = await _get_attachment_metadata_checked(client, sys_id)
        return format_response(data=mask_sensitive_fields(metadata), correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def attachment_download(sys_id: str, *, correlation_id: str = "") -> str:
        """Download attachment content by attachment sys_id.

        Args:
            sys_id: The sys_id of the attachment to download.
        """
        validate_sys_id(sys_id)
        async with ServiceNowClient(settings, auth_provider) as client:
            metadata = await _get_attachment_metadata_checked(client, sys_id)
            ensure_attachment_size_value_within_limit(get_attachment_size_bytes(metadata), operation="download")
            content = _require_bytes_content(await client.download_attachment(sys_id))

        ensure_attachment_size_within_limit(content, operation="download")
        masked_metadata = mask_sensitive_fields(metadata)
        return format_response(
            data=build_attachment_download_payload(masked_metadata, content),
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def attachment_download_by_name(
        table_name: str,
        table_sys_id: str,
        file_name: str,
        *,
        correlation_id: str = "",
    ) -> str:
        """Download attachment content by source record and file name.

        Args:
            table_name: The source table name.
            table_sys_id: The source record sys_id.
            file_name: The attachment file name.
        """
        validate_identifier(table_name)
        validate_sys_id(table_sys_id)
        check_table_access(table_name)

        async with ServiceNowClient(settings, auth_provider) as client:
            metadata, attachment_sys_id, warnings = await _get_attachment_metadata_by_name_checked(
                client,
                table_name,
                table_sys_id,
                file_name,
            )
            ensure_attachment_size_value_within_limit(get_attachment_size_bytes(metadata), operation="download")
            content = _require_bytes_content(await client.download_attachment(attachment_sys_id))

        ensure_attachment_size_within_limit(content, operation="download")
        masked_metadata = mask_sensitive_fields(metadata)
        return format_response(
            data=build_attachment_download_payload(masked_metadata, content),
            correlation_id=correlation_id,
            warnings=warnings,
        )
