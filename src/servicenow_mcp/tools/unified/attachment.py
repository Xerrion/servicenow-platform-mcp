"""Unified ``attachment`` (read) and ``attachment_write`` action-dispatching tools.

Folds the four legacy read tools (``attachment_list`` / ``attachment_get`` /
``attachment_download`` / ``attachment_download_by_name``) and the two write
tools (``attachment_upload`` / ``attachment_delete``) into a pair of
action-dispatching surfaces:

* ``attachment(action, ...)`` — dispatches on ``action``: list / get / download
  / download_by_name.
* ``attachment_write(action, ...)`` — dispatches on ``action``: upload / delete.

Old tools remain registered alongside until Phase 3b retires them.
"""

from __future__ import annotations

from typing import Final

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.errors import NotFoundError
from servicenow_mcp.policy import (
    check_table_access,
    gate_write,
    mask_sensitive_fields,
    production_write_blocked,
)
from servicenow_mcp.tools._attachment_common import (
    MAX_ATTACHMENT_BYTES,
    build_attachment_download_payload,
    decode_content_base64,
    ensure_attachment_size_value_within_limit,
    ensure_attachment_size_within_limit,
    get_attachment_size_bytes,
    get_attachment_sys_id,
    get_attachment_table_name,
)
from servicenow_mcp.utils import ServiceNowQuery, format_response, validate_identifier, validate_sys_id


TOOL_NAMES: list[str] = ["attachment", "attachment_write"]

_VALID_READ_ACTIONS: Final[frozenset[str]] = frozenset({"list", "get", "download", "download_by_name"})
_VALID_WRITE_ACTIONS: Final[frozenset[str]] = frozenset({"upload", "delete"})


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


def _err(correlation_id: str, message: str) -> str:
    """Return a serialized error envelope with the given message."""
    return format_response(data=None, correlation_id=correlation_id, status="error", error=message)


# ---------------------------------------------------------------------------
# Read-side argument parsing (parse-don't-validate)
# ---------------------------------------------------------------------------


def _validate_read_args(
    action: str,
    sys_id: str,
    table: str,
    table_sys_id: str,
    file_name: str,
    correlation_id: str,
) -> str | None:
    """Return error envelope if the action/argument combination is invalid."""
    if action not in _VALID_READ_ACTIONS:
        return _err(
            correlation_id,
            f"Unknown action {action!r}. Valid actions: {sorted(_VALID_READ_ACTIONS)}.",
        )

    if action == "list":
        if not table:
            return _err(correlation_id, "table is required for action='list'.")
        if not table_sys_id:
            return _err(correlation_id, "table_sys_id is required for action='list'.")
        return None

    if action == "get":
        if not sys_id:
            return _err(correlation_id, "sys_id is required for action='get'.")
        return None

    if action == "download":
        if not sys_id:
            return _err(correlation_id, "sys_id is required for action='download'.")
        return None

    # download_by_name
    if not table:
        return _err(correlation_id, "table is required for action='download_by_name'.")
    if not table_sys_id:
        return _err(correlation_id, "table_sys_id is required for action='download_by_name'.")
    if not file_name:
        return _err(correlation_id, "file_name is required for action='download_by_name'.")
    return None


def _validate_write_args(
    action: str,
    table: str,
    table_sys_id: str,
    file_name: str,
    content_base64: str,
    sys_id: str,
    correlation_id: str,
) -> str | None:
    """Return error envelope if the write action/argument combination is invalid."""
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

    # delete
    if not sys_id:
        return _err(correlation_id, "sys_id is required for action='delete'.")
    return None


# ---------------------------------------------------------------------------
# Read-side execution helpers
# ---------------------------------------------------------------------------


async def _run_list(
    client: ServiceNowClient,
    table: str,
    table_sys_id: str,
    correlation_id: str,
) -> str:
    """Execute the ``list`` action: list attachment metadata for a parent record."""
    query = ServiceNowQuery().equals("table_name", table).equals("table_sys_id", table_sys_id).build()
    result = await client.list_attachments(query=query, limit=100, offset=0)
    masked = [mask_sensitive_fields(record) for record in result["records"]]
    return format_response(
        data=masked,
        correlation_id=correlation_id,
        pagination={"offset": 0, "limit": 100, "total": len(masked)},
    )


async def _run_get(client: ServiceNowClient, sys_id: str, correlation_id: str) -> str:
    """Execute the ``get`` action: return masked metadata for a single attachment."""
    metadata = await client.get_attachment(sys_id)
    check_table_access(get_attachment_table_name(metadata))
    return format_response(data=mask_sensitive_fields(metadata), correlation_id=correlation_id)


async def _run_download(client: ServiceNowClient, sys_id: str, correlation_id: str) -> str:
    """Execute the ``download`` action: metadata-first then payload."""
    metadata = await client.get_attachment(sys_id)
    check_table_access(get_attachment_table_name(metadata))
    ensure_attachment_size_value_within_limit(get_attachment_size_bytes(metadata), operation="download")

    content = await client.download_attachment(sys_id)
    if not isinstance(content, bytes):
        raise TypeError("Attachment download content must be bytes")
    ensure_attachment_size_within_limit(content, operation="download")

    return format_response(
        data=build_attachment_download_payload(mask_sensitive_fields(metadata), content),
        correlation_id=correlation_id,
    )


async def _run_download_by_name(
    client: ServiceNowClient,
    table: str,
    table_sys_id: str,
    file_name: str,
    correlation_id: str,
) -> str:
    """Execute the ``download_by_name`` action: resolve via metadata then download.

    The metadata lookup is the source of truth for the attachment ``sys_id`` and
    its owning table; we never trust the caller-supplied path.
    """
    base_query = (
        ServiceNowQuery()
        .equals("table_name", table)
        .equals("table_sys_id", table_sys_id)
        .equals("file_name", file_name)
        .build()
    )
    order_clause = ServiceNowQuery().order_by("sys_created_on").build()
    query = f"{base_query}^{order_clause}"

    result = await client.query_records(
        "sys_attachment",
        query,
        fields=["sys_id", "table_name", "table_sys_id", "file_name", "content_type", "size_bytes"],
        limit=2,
    )
    records = result["records"]
    if not records:
        raise NotFoundError(f"Attachment '{file_name}' was not found for table '{table}' and record '{table_sys_id}'")

    metadata = records[0]
    attachment_sys_id = get_attachment_sys_id(metadata)
    check_table_access(get_attachment_table_name(metadata))
    ensure_attachment_size_value_within_limit(get_attachment_size_bytes(metadata), operation="download")

    content = await client.download_attachment(attachment_sys_id)
    if not isinstance(content, bytes):
        raise TypeError("Attachment download content must be bytes")
    ensure_attachment_size_within_limit(content, operation="download")

    warnings: list[str] | None = None
    if len(records) > 1:
        warnings = ["Multiple attachments matched; returned the earliest created attachment"]

    return format_response(
        data=build_attachment_download_payload(mask_sensitive_fields(metadata), content),
        correlation_id=correlation_id,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Write-side execution helpers
# ---------------------------------------------------------------------------


def _estimate_decoded_size(content_base64: str) -> int:
    """Approximate decoded byte length without actually decoding the payload.

    Each base64 character encodes 6 bits; four characters carry 3 bytes. The
    approximation rejects clearly-oversize payloads before the (potentially
    expensive) decode runs.
    """
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
    """Execute the ``upload`` action: decode and upload base64 content."""
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
    """Execute the ``delete`` action: metadata-first to learn the owning table.

    The env-level production gate has already fired; here we apply the
    table-specific deny-list gate after metadata resolves the owning table.
    """
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


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> None:
    """Register the unified ``attachment`` and ``attachment_write`` tools.

    ``choices`` is unused here but accepted for unified-loader contract parity.
    """
    del choices  # unused; signature retained for loader parity

    @mcp.tool()
    @tool_handler
    async def attachment(
        action: str,
        sys_id: str = "",
        table: str = "",
        table_sys_id: str = "",
        file_name: str = "",
        *,
        correlation_id: str = "",
    ) -> str:
        """Read attachments. action: 'list' | 'get' | 'download' | 'download_by_name'.

        Args:
            action: One of: list, get, download, download_by_name.
            sys_id: Attachment sys_id (for get, download).
            table: Parent table (for list, download_by_name).
            table_sys_id: Parent record sys_id (for list, download_by_name).
            file_name: File name (for download_by_name).
        """
        # --- 1. Argument validation (early exit) -------------------------
        err = _validate_read_args(action, sys_id, table, table_sys_id, file_name, correlation_id)
        if err:
            return err

        # --- 2. Identifier-shape validation ------------------------------
        if sys_id:
            validate_sys_id(sys_id)
        if table_sys_id:
            validate_sys_id(table_sys_id)

        # --- 3. Table access policy (table-bearing actions only) ---------
        if action in {"list", "download_by_name"}:
            validate_identifier(table)
            check_table_access(table)

        # --- 4. Dispatch -------------------------------------------------
        async with ServiceNowClient(settings, auth_provider) as client:
            if action == "list":
                return await _run_list(client, table, table_sys_id, correlation_id)
            if action == "get":
                return await _run_get(client, sys_id, correlation_id)
            if action == "download":
                return await _run_download(client, sys_id, correlation_id)
            return await _run_download_by_name(client, table, table_sys_id, file_name, correlation_id)

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
        # --- 1. Argument validation (early exit) -------------------------
        err = _validate_write_args(action, table, table_sys_id, file_name, content_base64, sys_id, correlation_id)
        if err:
            return err

        # --- 2. Per-action gating + dispatch -----------------------------
        if action == "upload":
            validate_sys_id(table_sys_id)

            blocked = gate_write(table, settings, correlation_id)
            if blocked:
                return blocked

            # Pre-decode size guard rejects clearly-oversize payloads before
            # the actual base64 decode allocates the full byte buffer.
            if _estimate_decoded_size(content_base64) > MAX_ATTACHMENT_BYTES:
                return _err(
                    correlation_id,
                    f"Attachment upload size {_estimate_decoded_size(content_base64)} bytes exceeds the maximum "
                    f"supported size of {MAX_ATTACHMENT_BYTES} bytes",
                )

            async with ServiceNowClient(settings, auth_provider) as client:
                return await _run_upload(
                    client,
                    table,
                    table_sys_id,
                    file_name,
                    content_base64,
                    content_type,
                    correlation_id,
                )

        # delete
        validate_sys_id(sys_id)

        # Env-level block fires BEFORE the metadata fetch so production never
        # leaks a network round-trip for an attachment whose owning table we
        # cannot know without asking. Table-specific deny-list checks happen
        # post-fetch via gate_write inside _run_delete.
        env_blocked = production_write_blocked(settings, correlation_id)
        if env_blocked:
            return env_blocked

        async with ServiceNowClient(settings, auth_provider) as client:
            return await _run_delete(client, sys_id, settings, correlation_id)
