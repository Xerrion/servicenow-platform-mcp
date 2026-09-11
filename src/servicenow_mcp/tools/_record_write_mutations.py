"""Create, update, and delete execution for unified record writes."""

from __future__ import annotations

from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.policy import mask_sensitive_fields
from servicenow_mcp.response import format_response
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.tools._record_helpers import _build_update_diff, _check_mandatory_or_error
from servicenow_mcp.tools._record_write_preview import RecordWritePreviewManager
from servicenow_mcp.tools._record_write_validation import WriteRequest


async def _run_create(
    client: ServiceNowClient,
    request: WriteRequest,
    data: dict[str, Any],
    previews: RecordWritePreviewManager,
    dictionary: DictionaryRegistry,
) -> str:
    error = await _check_mandatory_or_error(client, request.table, data, dictionary)
    if error:
        return error

    if request.preview:
        token = await previews.create({"action": "create", "table": request.table, "data": data})
        return format_response(
            data={
                "action": "create",
                "table": request.table,
                "preview_token": token,
                "preview": {"data": mask_sensitive_fields(data)},
            },
        )

    created = await client.create_record(request.table, data)
    return format_response(
        data={
            "action": "create",
            "table": request.table,
            "sys_id": created["sys_id"],
            "record": mask_sensitive_fields(created),
        },
    )


async def _run_update(
    client: ServiceNowClient,
    request: WriteRequest,
    data: dict[str, Any],
    previews: RecordWritePreviewManager,
) -> str:
    if request.preview:
        current = await client.get_record(request.table, request.sys_id)
        diff = _build_update_diff(data, current)
        token = await previews.create(
            {"action": "update", "table": request.table, "sys_id": request.sys_id, "changes": data},
        )
        return format_response(
            data={
                "action": "update",
                "table": request.table,
                "sys_id": request.sys_id,
                "preview_token": token,
                "preview": {"diff": diff},
            },
        )

    updated = await client.update_record(request.table, request.sys_id, data)
    return format_response(
        data={
            "action": "update",
            "table": request.table,
            "sys_id": request.sys_id,
            "record": mask_sensitive_fields(updated),
        },
    )


async def _run_delete(
    client: ServiceNowClient,
    request: WriteRequest,
    previews: RecordWritePreviewManager,
) -> str:
    if request.preview:
        snapshot = await client.get_record(request.table, request.sys_id)
        token = await previews.create(
            {
                "action": "delete",
                "table": request.table,
                "sys_id": request.sys_id,
                "record_snapshot": snapshot,
            },
        )
        return format_response(
            data={
                "action": "delete",
                "table": request.table,
                "sys_id": request.sys_id,
                "preview_token": token,
                "preview": {"record_snapshot": mask_sensitive_fields(snapshot)},
            },
        )

    await client.delete_record(request.table, request.sys_id)
    return format_response(
        data={"action": "delete", "table": request.table, "sys_id": request.sys_id, "deleted": True},
    )


async def run_record_write(
    client: ServiceNowClient,
    request: WriteRequest,
    data: dict[str, Any],
    previews: RecordWritePreviewManager,
    dictionary: DictionaryRegistry,
) -> str:
    """Execute or stage a validated record-write request."""
    if request.action == "create":
        return await _run_create(client, request, data, previews, dictionary)
    if request.action == "update":
        return await _run_update(client, request, data, previews)
    return await _run_delete(client, request, previews)


async def apply_preview_payload(
    client: ServiceNowClient,
    payload: dict[str, Any],
    dictionary: DictionaryRegistry,
) -> str:
    """Execute a consumed preview payload."""
    action = payload["action"]
    table = payload["table"]

    if action == "create":
        error = await _check_mandatory_or_error(client, table, payload["data"], dictionary)
        if error:
            return error
        result = await client.create_record(table, payload["data"])
        return format_response(
            data={
                "action": "create",
                "table": table,
                "sys_id": result["sys_id"],
                "record": mask_sensitive_fields(result),
            },
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
        )

    if action == "delete":
        sys_id = payload["sys_id"]
        await client.delete_record(table, sys_id)
        return format_response(data={"action": "delete", "table": table, "sys_id": sys_id, "deleted": True})

    return format_response(data=None, status="error", error=f"Unknown preview action: {action!r}")
