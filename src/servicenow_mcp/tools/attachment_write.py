"""Unified ``attachment_write`` action-dispatching tool."""

from __future__ import annotations

from typing import Final

from mcp.server import MCPServer

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import gate_write, production_write_blocked
from servicenow_mcp.tools._attachment_common import (
    MAX_ATTACHMENT_BYTES,
    decode_content_base64,
    ensure_attachment_size_within_limit,
    get_attachment_table_name,
)
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.utils import format_response, validate_identifier, validate_sys_id


TOOL_NAMES: list[str] = ["attachment_write"]

_VALID_WRITE_ACTIONS: Final[frozenset[str]] = frozenset({"upload", "delete"})


def _err(correlation_id: str, message: str) -> str:
    """Return a serialized error envelope with the given message."""
    return format_response(data=None, correlation_id=correlation_id, status="error", error=message)


def _validate_write_args(
    action: str,
    table: str,
    table_sys_id: str,
    file_name: str,
    content_base64: str,
    sys_id: str,
    correlation_id: str,
) -> str | None:
    """Return an error envelope if the write action arguments are invalid."""
    if action not in _VALID_WRITE_ACTIONS:
        return _err(
            correlation_id,
            f"Unknown action {action!r}. Valid actions: {sorted(_VALID_WRITE_ACTIONS)}.",
        )

    if action == "upload":
        if not table:
            return _err(correlation_id, "table is required for action='upload'.")
        if not table_sys_id:
            return _err(correlation_id, "table_sys_id is required for action='upload'.")
        if not file_name:
            return _err(correlation_id, "file_name is required for action='upload'.")
        if not content_base64:
            return _err(correlation_id, "content_base64 is required for action='upload'.")
        return None

    if not sys_id:
        return _err(correlation_id, "sys_id is required for action='delete'.")
    return None


def _estimate_decoded_size(content_base64: str) -> int:
    """Approximate decoded byte length without decoding the payload."""
    return len(content_base64) * 3 // 4


async def _run_upload(
    client: ServiceNowClient,
    table: str,
    table_sys_id: str,
    file_name: str,
    content_base64: str,
    content_type: str,
    correlation_id: str,
) -> str:
    """Decode and upload base64 attachment content."""
    content = decode_content_base64(content_base64)
    ensure_attachment_size_within_limit(content, operation="upload")

    result = await client.upload_attachment(
        table_name=table,
        table_sys_id=table_sys_id,
        file_name=file_name,
        content=content,
        content_type=content_type,
    )
    return format_response(data=result, correlation_id=correlation_id)


async def _run_delete(
    client: ServiceNowClient,
    sys_id: str,
    settings: Settings,
    correlation_id: str,
) -> str:
    """Resolve the owning table, apply the table gate, and delete the attachment."""
    metadata = await client.get_attachment(sys_id)
    table_name = get_attachment_table_name(metadata)

    blocked = gate_write(table_name, settings, correlation_id)
    if blocked:
        return blocked

    await client.delete_attachment(sys_id)
    return format_response(
        data={"sys_id": sys_id, "table_name": table_name, "deleted": True},
        correlation_id=correlation_id,
    )


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    """Register the unified ``attachment_write`` tool.

    ``choices`` and ``dictionary`` are accepted for loader contract parity.
    """
    del choices, dictionary
    client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))

    @mcp.tool()
    @tool_handler
    async def attachment_write(
        action: str,
        table: str = "",
        table_sys_id: str = "",
        file_name: str = "",
        content_base64: str = "",
        content_type: str = "application/octet-stream",
        sys_id: str = "",
        *,
        correlation_id: str = "",
    ) -> str:
        """Write attachments. action: 'upload' | 'delete'.

        Args:
            action: 'upload' or 'delete'.
            table: Parent table (upload).
            table_sys_id: Parent record sys_id (upload).
            file_name: Attachment file name (upload).
            content_base64: Base64-encoded file bytes (upload).
            content_type: MIME type (upload, default 'application/octet-stream').
            sys_id: Attachment sys_id (delete).
        """
        err = _validate_write_args(action, table, table_sys_id, file_name, content_base64, sys_id, correlation_id)
        if err:
            return err

        if action == "upload":
            validate_identifier(table)
            validate_sys_id(table_sys_id)

            blocked = gate_write(table, settings, correlation_id)
            if blocked:
                return blocked

            estimated_size = _estimate_decoded_size(content_base64)
            if estimated_size > MAX_ATTACHMENT_BYTES:
                return _err(
                    correlation_id,
                    f"Attachment upload size {estimated_size} bytes exceeds the maximum supported size of "
                    f"{MAX_ATTACHMENT_BYTES} bytes",
                )

            async with client_factory() as client:
                return await _run_upload(
                    client,
                    table,
                    table_sys_id,
                    file_name,
                    content_base64,
                    content_type,
                    correlation_id,
                )

        validate_sys_id(sys_id)

        env_blocked = production_write_blocked(settings, correlation_id)
        if env_blocked:
            return env_blocked

        async with client_factory() as client:
            return await _run_delete(client, sys_id, settings, correlation_id)
