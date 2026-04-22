"""Attachment write tools for ServiceNow attachments."""

import logging

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import check_table_access, write_gate
from servicenow_mcp.tools._attachment_common import (
    decode_content_base64,
    ensure_attachment_size_within_limit,
    ensure_base64_size_within_limit,
    get_attachment_table_name,
)
from servicenow_mcp.utils import format_response, validate_identifier, validate_sys_id


logger = logging.getLogger(__name__)


TOOL_NAMES: list[str] = [
    "attachment_upload",
    "attachment_delete",
]


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register attachment write tools on the MCP server."""

    @mcp.tool()
    @tool_handler
    async def attachment_upload(
        table_name: str,
        table_sys_id: str,
        file_name: str,
        content_base64: str,
        content_type: str = "application/octet-stream",
        encryption_context: str | None = None,
        creation_time: str | None = None,
        *,
        correlation_id: str = "",
    ) -> str:
        """Upload a new attachment using base64-encoded content.

        Args:
            table_name: The table to attach the file to.
            table_sys_id: The sys_id of the source record.
            file_name: The attachment file name.
            content_base64: Base64-encoded attachment content.
            content_type: MIME type for the upload.
            encryption_context: Optional encryption context.
            creation_time: Optional custom attachment creation time.
        """
        validate_identifier(table_name)
        validate_sys_id(table_sys_id)
        check_table_access(table_name)

        blocked = write_gate(table_name, settings, correlation_id)
        if blocked:
            return blocked

        ensure_base64_size_within_limit(content_base64, operation="upload")
        content = decode_content_base64(content_base64)
        ensure_attachment_size_within_limit(content, operation="upload")

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.upload_attachment(
                table_name=table_name,
                table_sys_id=table_sys_id,
                file_name=file_name,
                content=content,
                content_type=content_type,
                encryption_context=encryption_context,
                creation_time=creation_time,
            )

        return format_response(data=result, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def attachment_delete(sys_id: str, *, correlation_id: str = "") -> str:
        """Delete an attachment by attachment sys_id.

        Args:
            sys_id: The sys_id of the attachment to delete.
        """
        validate_sys_id(sys_id)

        async with ServiceNowClient(settings, auth_provider) as client:
            metadata = await client.get_attachment(sys_id)
            table_name = get_attachment_table_name(metadata)
            check_table_access(table_name)

            blocked = write_gate(table_name, settings, correlation_id)
            if blocked:
                return blocked

            await client.delete_attachment(sys_id)

        return format_response(
            data={"sys_id": sys_id, "table_name": table_name, "deleted": True},
            correlation_id=correlation_id,
        )
