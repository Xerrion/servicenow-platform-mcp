"""Attachment read tools for ServiceNow attachments."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.errors import NotFoundError
from servicenow_mcp.policy import check_table_access, enforce_query_safety
from servicenow_mcp.utils import ServiceNowQuery, format_response, validate_identifier, validate_sys_id

from ._attachment_common import (
    build_attachment_download_payload,
    ensure_attachment_size_within_limit,
    get_attachment_sys_id,
    get_attachment_table_name,
)


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

        Args:
            table_name: Optional table name to filter attachments by.
            table_sys_id: Optional source record sys_id to filter attachments by.
            file_name: Optional attachment file name to filter by.
            limit: Maximum number of attachments to return.
            offset: Number of matching attachments to skip.
            order_by: Field to sort by.
        """
        if table_name:
            validate_identifier(table_name)
            check_table_access(table_name)
        if table_sys_id:
            validate_sys_id(table_sys_id)
        if order_by:
            validate_identifier(order_by)

        query = _build_attachment_query(table_name, table_sys_id, file_name)
        if order_by:
            order_query = ServiceNowQuery().order_by(order_by).build()
            query = f"{query}^{order_query}" if query else order_query
        safety = enforce_query_safety("sys_attachment", query, limit, settings)
        effective_limit = safety["limit"]
        warnings = [f"Limit capped at {effective_limit}"] if effective_limit < limit else None

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.list_attachments(
                query,
                effective_limit,
                offset,
            )

        return format_response(
            data=result["records"],
            correlation_id=correlation_id,
            pagination={
                "offset": offset,
                "limit": effective_limit,
                "total": result["count"],
            },
            warnings=warnings,
        )

    @mcp.tool()
    @tool_handler
    async def attachment_get(sys_id: str, *, correlation_id: str = "") -> str:
        """Fetch attachment metadata by attachment sys_id.

        Args:
            sys_id: The sys_id of the attachment.
        """
        validate_sys_id(sys_id)
        async with ServiceNowClient(settings, auth_provider) as client:
            metadata = await _get_attachment_metadata_checked(client, sys_id)
        return format_response(data=metadata, correlation_id=correlation_id)

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
            content: bytes = await client.download_attachment(sys_id)

        ensure_attachment_size_within_limit(content, operation="download")
        return format_response(
            data=build_attachment_download_payload(metadata, content),
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
            content: bytes = await client.download_attachment(attachment_sys_id)

        ensure_attachment_size_within_limit(content, operation="download")
        return format_response(
            data=build_attachment_download_payload(metadata, content),
            correlation_id=correlation_id,
            warnings=warnings,
        )
