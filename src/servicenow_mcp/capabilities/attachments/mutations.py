"""Attachment review and apply-time ownership checks."""

import hashlib
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.attachments.common import (
    ensure_attachment_size_within_limit,
    get_attachment_table_name,
)
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.mutations.models import AttachmentCommand, fingerprint
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient


async def preview(
    command: AttachmentCommand, client: ServiceNowClient
) -> tuple[dict[str, Any], str, str | None]:
    if command.action == "upload":
        ensure_attachment_size_within_limit(command.content or b"", operation="upload")
        await client.get_record(
            command.table or "", command.table_sys_id or "", fields=["sys_id"]
        )
        return (
            {
                "table": command.table,
                "table_sys_id": command.table_sys_id,
                "file_name": command.file_name,
                "content_type": command.content_type,
                "size_bytes": len(command.content or b""),
                "sha256": hashlib.sha256(command.content or b"").hexdigest(),
            },
            command.table or "",
            None,
        )
    metadata = await client.get_attachment(command.sys_id or "")
    table = get_attachment_table_name(metadata)
    check_table_access(table)
    return mask_sensitive_fields(metadata), table, fingerprint(metadata)


async def apply(
    command: AttachmentCommand,
    table: str,
    expected: str | None,
    client: ServiceNowClient,
) -> dict[str, Any]:
    if command.action == "upload":
        await client.get_record(table, command.table_sys_id or "", fields=["sys_id"])
        ensure_attachment_size_within_limit(command.content or b"", operation="upload")
        uploaded = await client.upload_attachment(
            table_name=table,
            table_sys_id=command.table_sys_id or "",
            file_name=command.file_name or "",
            content=command.content or b"",
            content_type=command.content_type,
        )
        if not isinstance(uploaded, dict) or not uploaded.get("sys_id"):
            raise CapabilityError(
                "Attachment response was incomplete. Remote outcome is unknown. Check ServiceNow before another "
                "preview.",
                "UNKNOWN_OUTCOME",
            )
        return mask_sensitive_fields(uploaded)
    metadata = await client.get_attachment(command.sys_id or "")
    if (
        get_attachment_table_name(metadata) != table
        or fingerprint(metadata) != expected
    ):
        raise CapabilityError(
            "The attachment changed since preview. Create a new preview.",
            "STALE_PREVIEW",
        )
    await client.delete_attachment(command.sys_id or "")
    return {"sys_id": command.sys_id, "table_name": table, "deleted": True}
