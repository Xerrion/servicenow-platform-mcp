"""Record-level write operations for ServiceNow tables."""

import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    MASK_VALUE,
    check_table_access,
    is_sensitive_field,
    mask_sensitive_fields,
    write_gate,
)
from servicenow_mcp.state import PreviewTokenStore
from servicenow_mcp.utils import format_response, validate_identifier, validate_sys_id


logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Module-scope helpers (extracted from register_tools closure)
# ------------------------------------------------------------------


async def _check_mandatory_fields(
    client: ServiceNowClient,
    table: str,
    data: dict[str, Any],
) -> list[str]:
    """Return list of mandatory field names missing from *data*.

    Best-effort: if metadata fetch fails, logs a warning and returns
    an empty list so the create can proceed (ServiceNow will still
    validate server-side).
    """
    try:
        metadata = await client.get_metadata(table)
    except Exception:
        logger.warning(
            "Failed to fetch metadata for mandatory field check on table '%s'",
            table,
        )
        return []
    mandatory_fields = [
        entry["element"] for entry in metadata if entry.get("mandatory") == "true" and entry.get("element")
    ]
    return [f for f in mandatory_fields if f not in data]


async def _check_mandatory_or_error(
    client: ServiceNowClient,
    table: str,
    data: dict[str, Any],
    correlation_id: str,
) -> str | None:
    """Check for missing mandatory fields and return error response if any, else None."""
    missing = await _check_mandatory_fields(client, table, data)
    if missing:
        return format_response(
            data={"table": table, "missing_fields": missing},
            correlation_id=correlation_id,
            status="error",
            error=f"Missing mandatory fields for table '{table}': {', '.join(missing)}",
        )
    return None


async def _execute_apply_action(
    client: ServiceNowClient,
    payload: dict[str, Any],
    table: str,
    correlation_id: str,
) -> str:
    """Execute a previewed create/update/delete action."""
    action = payload["action"]

    if action == "create":
        err = await _check_mandatory_or_error(client, table, payload["data"], correlation_id)
        if err:
            return err
        result = await client.create_record(table, payload["data"])
        return format_response(
            data={
                "action": "create",
                "table": table,
                "sys_id": result["sys_id"],
                "record": mask_sensitive_fields(result),
            },
            correlation_id=correlation_id,
        )

    if action == "update":
        sys_id = payload["sys_id"]
        result = await client.update_record(table, sys_id, payload["changes"])
        return format_response(
            data={
                "action": "update",
                "table": table,
                "sys_id": sys_id,
                "record": mask_sensitive_fields(result),
            },
            correlation_id=correlation_id,
        )

    if action == "delete":
        sys_id = payload["sys_id"]
        await client.delete_record(table, sys_id)
        return format_response(
            data={
                "action": "delete",
                "table": table,
                "sys_id": sys_id,
                "deleted": True,
            },
            correlation_id=correlation_id,
        )

    return format_response(
        data=None,
        correlation_id=correlation_id,
        status="error",
        error=f"Unknown preview action: '{action}'",
    )


def _build_update_diff(
    changes_dict: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, dict[str, str]]:
    """Build a field-level diff for a preview update."""
    diff: dict[str, dict[str, str]] = {}
    for field, new_value in changes_dict.items():
        old_value = current.get(field, "")
        if is_sensitive_field(field):
            diff[field] = {"old": MASK_VALUE, "new": MASK_VALUE}
        else:
            diff[field] = {"old": old_value, "new": new_value}
    return diff


TOOL_NAMES: list[str] = [
    "record_create",
    "record_preview_create",
    "record_update",
    "record_preview_update",
    "record_delete",
    "record_preview_delete",
    "record_apply",
]


def _validate_write_request(table: str, settings: Settings, correlation_id: str) -> str | None:
    """Validate table access and write permissions, returning an error envelope if blocked."""
    validate_identifier(table)
    check_table_access(table)
    return write_gate(table, settings, correlation_id)


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register record write operation tools on the MCP server."""

    # In-memory preview token store, shared across preview/apply tools via closure
    preview_store = PreviewTokenStore()

    # ------------------------------------------------------------------
    # Write tools (CRUD)
    # ------------------------------------------------------------------

    @mcp.tool()
    @tool_handler
    async def record_create(table: str, data: str, *, correlation_id: str = "") -> str:
        """Create a new record in a ServiceNow table.

        Args:
            table: The table to create the record in (e.g. 'incident').
            data: A JSON string of field-value pairs for the new record.
        """
        blocked = _validate_write_request(table, settings, correlation_id)
        if blocked:
            return blocked

        record_data = json.loads(data)

        async with ServiceNowClient(settings, auth_provider) as client:
            err = await _check_mandatory_or_error(client, table, record_data, correlation_id)
            if err:
                return err
            created = await client.create_record(table, record_data)

        return format_response(
            data={
                "table": table,
                "sys_id": created["sys_id"],
                "record": mask_sensitive_fields(created),
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def record_preview_create(table: str, data: str, *, correlation_id: str = "") -> str:
        """Preview a record creation without executing it. Returns a token to apply later.

        Args:
            table: The table to create the record in (e.g. 'incident').
            data: A JSON string of field-value pairs for the new record.
        """
        blocked = _validate_write_request(table, settings, correlation_id)
        if blocked:
            return blocked

        record_data = json.loads(data)

        async with ServiceNowClient(settings, auth_provider) as client:
            err = await _check_mandatory_or_error(client, table, record_data, correlation_id)
            if err:
                return err

        # Store for later apply - no further HTTP call needed
        token = preview_store.create(
            {
                "action": "create",
                "table": table,
                "data": record_data,
            }
        )

        return format_response(
            data={
                "token": token,
                "table": table,
                "action": "create",
                "data": mask_sensitive_fields(record_data),
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def record_update(table: str, sys_id: str, changes: str, *, correlation_id: str = "") -> str:
        """Update an existing record in a ServiceNow table.

        Args:
            table: The table containing the record (e.g. 'incident').
            sys_id: The sys_id of the record to update.
            changes: A JSON string of field-value pairs to update.
        """
        blocked = _validate_write_request(table, settings, correlation_id)
        if blocked:
            return blocked

        validate_sys_id(sys_id)

        changes_dict = json.loads(changes)

        async with ServiceNowClient(settings, auth_provider) as client:
            updated = await client.update_record(table, sys_id, changes_dict)

        return format_response(
            data={
                "table": table,
                "sys_id": sys_id,
                "record": mask_sensitive_fields(updated),
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def record_preview_update(table: str, sys_id: str, changes: str, *, correlation_id: str = "") -> str:
        """Preview an update to a record: shows field-level diff and returns a token to apply.

        Args:
            table: The table containing the record.
            sys_id: The sys_id of the record to update.
            changes: A JSON string of field-value pairs to change.
        """
        blocked = _validate_write_request(table, settings, correlation_id)
        if blocked:
            return blocked

        validate_sys_id(sys_id)

        changes_dict = json.loads(changes)

        async with ServiceNowClient(settings, auth_provider) as client:
            current = await client.get_record(table, sys_id)

        diff = _build_update_diff(changes_dict, current)

        # Store preview for later apply
        token = preview_store.create(
            {
                "action": "update",
                "table": table,
                "sys_id": sys_id,
                "changes": changes_dict,
            }
        )

        return format_response(
            data={
                "token": token,
                "table": table,
                "sys_id": sys_id,
                "diff": diff,
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def record_delete(table: str, sys_id: str, *, correlation_id: str = "") -> str:
        """Delete a record from a ServiceNow table.

        Args:
            table: The table containing the record (e.g. 'incident').
            sys_id: The sys_id of the record to delete.
        """
        blocked = _validate_write_request(table, settings, correlation_id)
        if blocked:
            return blocked

        validate_sys_id(sys_id)

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.delete_record(table, sys_id)

        return format_response(
            data={
                "table": table,
                "sys_id": sys_id,
                "deleted": True,
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def record_preview_delete(table: str, sys_id: str, *, correlation_id: str = "") -> str:
        """Preview a record deletion: shows the record that will be deleted and returns a token to confirm.

        Args:
            table: The table containing the record (e.g. 'incident').
            sys_id: The sys_id of the record to delete.
        """
        blocked = _validate_write_request(table, settings, correlation_id)
        if blocked:
            return blocked

        validate_sys_id(sys_id)

        async with ServiceNowClient(settings, auth_provider) as client:
            record = await client.get_record(table, sys_id)

        # Store preview for later apply
        token = preview_store.create(
            {
                "action": "delete",
                "table": table,
                "sys_id": sys_id,
                "record_snapshot": record,
            }
        )

        return format_response(
            data={
                "token": token,
                "table": table,
                "sys_id": sys_id,
                "record_snapshot": mask_sensitive_fields(record),
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def record_apply(preview_token: str, *, correlation_id: str = "") -> str:
        """Apply a previously previewed action (create, update, or delete) using the preview token.

        Args:
            preview_token: The single-use token returned by record_preview_create, record_preview_update, or record_preview_delete.
        """
        # Consume the token (single-use)
        payload = preview_store.consume(preview_token)
        if payload is None:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error="Invalid or expired preview token",
            )

        table = payload["table"]

        # Defense in depth - re-check access and write gate
        check_table_access(table)
        blocked = write_gate(table, settings, correlation_id)
        if blocked:
            return blocked

        async with ServiceNowClient(settings, auth_provider) as client:
            return await _execute_apply_action(client, payload, table, correlation_id)
