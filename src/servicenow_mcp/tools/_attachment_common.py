"""Shared attachment helpers for validation, metadata parsing and size limits."""

import base64
import binascii
from typing import Any

from servicenow_mcp.utils import resolve_ref_value, validate_identifier, validate_sys_id


MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def ensure_attachment_size_value_within_limit(size_bytes: int, *, operation: str) -> None:
    """Raise ValueError when an attachment size exceeds the supported MCP transfer limit."""
    if size_bytes <= MAX_ATTACHMENT_BYTES:
        return
    raise ValueError(
        f"Attachment {operation} size {size_bytes} bytes exceeds the maximum supported size of "
        f"{MAX_ATTACHMENT_BYTES} bytes"
    )


def ensure_attachment_size_within_limit(content: bytes, *, operation: str) -> None:
    """Raise ValueError when attachment bytes exceed the supported MCP transfer limit."""
    ensure_attachment_size_value_within_limit(len(content), operation=operation)


def decode_content_base64(content_base64: str) -> bytes:
    """Decode validated base64 attachment content into raw bytes."""
    try:
        return base64.b64decode(content_base64, validate=True)
    except binascii.Error as exc:
        raise ValueError("Invalid base64 content") from exc


def encode_content_base64(content: bytes) -> str:
    """Encode attachment bytes for MCP transport."""
    return base64.b64encode(content).decode("ascii")


def get_attachment_field(metadata: dict[str, Any], field_name: str) -> str:
    """Return a required attachment metadata field as a normalized string."""
    value = resolve_ref_value(metadata.get(field_name, ""))
    if value:
        return value
    raise ValueError(f"Attachment metadata is missing required field '{field_name}'")


def get_attachment_sys_id(metadata: dict[str, Any]) -> str:
    """Return and validate the attachment sys_id from metadata."""
    sys_id = get_attachment_field(metadata, "sys_id")
    validate_sys_id(sys_id)
    return sys_id


def get_attachment_table_name(metadata: dict[str, Any]) -> str:
    """Return and validate the source table name from attachment metadata."""
    table_name = get_attachment_field(metadata, "table_name")
    validate_identifier(table_name)
    return table_name


def get_attachment_table_sys_id(metadata: dict[str, Any]) -> str:
    """Return and validate the source record sys_id from attachment metadata."""
    table_sys_id = get_attachment_field(metadata, "table_sys_id")
    validate_sys_id(table_sys_id)
    return table_sys_id


def get_attachment_size_bytes(metadata: dict[str, Any]) -> int:
    """Return and validate the attachment size from metadata."""
    raw_size = resolve_ref_value(metadata.get("size_bytes", ""))
    if raw_size == "":
        raise ValueError("Attachment metadata is missing required field 'size_bytes'")

    try:
        size_bytes = int(raw_size)
    except ValueError as exc:
        raise ValueError("Attachment metadata field 'size_bytes' must be an integer") from exc

    if size_bytes < 0:
        raise ValueError("Attachment metadata field 'size_bytes' must be non-negative")
    return size_bytes


def build_attachment_download_payload(metadata: dict[str, Any], content: bytes) -> dict[str, Any]:
    """Build a stable attachment download response payload."""
    return {
        "sys_id": get_attachment_sys_id(metadata),
        "table_name": get_attachment_table_name(metadata),
        "table_sys_id": get_attachment_table_sys_id(metadata),
        "file_name": get_attachment_field(metadata, "file_name"),
        "content_type": resolve_ref_value(metadata.get("content_type", "")),
        "size_bytes": len(content),
        "content_base64": encode_content_base64(content),
    }
