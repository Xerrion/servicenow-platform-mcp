"""Shared helpers for record-level write operations.

Single source of truth for the mandatory-field validation routine and the
update-diff builder. Imported by both the legacy ``record_write`` tool module
and the unified ``record_write`` / ``record_apply`` tools.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.errors import ForbiddenError, NotFoundError, ServerError
from servicenow_mcp.policy import MASK_VALUE, is_sensitive_field
from servicenow_mcp.utils import format_response


logger = logging.getLogger(__name__)


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
    except (NotFoundError, ForbiddenError, ServerError, httpx.HTTPError):
        # Metadata genuinely unavailable for this table or the instance is
        # unreachable; defer to ServiceNow's own server-side validation.
        # AuthError and other ServiceNowMCPError subclasses propagate so
        # genuine misconfiguration surfaces to the caller.
        logger.warning(
            "Metadata not available for table '%s'; skipping mandatory check",
            table,
            exc_info=True,
        )
        return []
    mandatory_fields = [
        entry["element"] for entry in metadata if entry.get("mandatory") == "true" and entry.get("element")
    ]
    return [f for f in mandatory_fields if not data.get(f)]


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
