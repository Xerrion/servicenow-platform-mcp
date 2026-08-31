"""Unified ``attachment`` read action-dispatching tool.

Folds the four legacy read tools (``attachment_list`` / ``attachment_get`` /
``attachment_download`` / ``attachment_download_by_name``) into one
action-dispatching surface:

* ``attachment(action, ...)`` — dispatches on ``action``: list / get / download
  / download_by_name.
"""

from __future__ import annotations

from typing import Final

from mcp.server import MCPServer

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.errors import NotFoundError
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.tools._attachment_common import (
    build_attachment_download_payload,
    ensure_attachment_size_value_within_limit,
    ensure_attachment_size_within_limit,
    get_attachment_size_bytes,
    get_attachment_sys_id,
    get_attachment_table_name,
)
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.utils import ServiceNowQuery, format_response, validate_identifier, validate_sys_id


TOOL_NAMES: list[str] = ["attachment"]

_VALID_READ_ACTIONS: Final[frozenset[str]] = frozenset({"list", "get", "download", "download_by_name"})


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


# ---------------------------------------------------------------------------
# Read-side identifier validation + dispatch
# ---------------------------------------------------------------------------


def _validate_read_identifier_shapes(
    action: str,
    sys_id: str,
    table_sys_id: str,
    table: str,
) -> None:
    """Validate identifier shapes and table access for the read-side actions.

    Raises ``ValueError`` from ``validate_sys_id`` / ``validate_identifier`` and
    ``PolicyError`` from ``check_table_access`` so ``safe_tool_call`` serializes
    the failure into an error envelope. ``_validate_read_args`` has already
    confirmed which identifiers are required for the requested action.
    """
    if sys_id:
        validate_sys_id(sys_id)
    if table_sys_id:
        validate_sys_id(table_sys_id)
    if action in {"list", "download_by_name"}:
        validate_identifier(table)
        check_table_access(table)


async def _dispatch_read_action(
    client: ServiceNowClient,
    action: str,
    sys_id: str,
    table: str,
    table_sys_id: str,
    file_name: str,
    correlation_id: str,
) -> str:
    """Dispatch a validated read-side action to its ``_run_*`` helper."""
    if action == "list":
        return await _run_list(client, table, table_sys_id, correlation_id)
    if action == "get":
        return await _run_get(client, sys_id, correlation_id)
    if action == "download":
        return await _run_download(client, sys_id, correlation_id)
    # download_by_name - implicit final branch matches _validate_read_args.
    return await _run_download_by_name(client, table, table_sys_id, file_name, correlation_id)


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
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    """Register the unified ``attachment`` read tool.

    ``choices`` is unused here but accepted for unified-loader contract parity.
    """
    del choices, dictionary  # unused; signature retained for loader parity
    client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))

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

        # --- 2. Identifier-shape + table-access validation ---------------
        _validate_read_identifier_shapes(action, sys_id, table_sys_id, table)

        # --- 3. Dispatch -------------------------------------------------
        async with client_factory() as client:
            return await _dispatch_read_action(client, action, sys_id, table, table_sys_id, file_name, correlation_id)
