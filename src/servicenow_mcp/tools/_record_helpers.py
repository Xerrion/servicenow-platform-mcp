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
from servicenow_mcp.utils import ServiceNowQuery, format_response, validate_sys_id


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


async def _resolve_record_sys_id(
    client: ServiceNowClient,
    table: str,
    sys_id: str,
    name: str,
    correlation_id: str,
) -> tuple[str | None, str | None]:
    """Resolve the target sys_id for ``record_read``.

    Returns ``(resolved_sys_id, None)`` on success and ``(None, error_envelope)``
    on failure. The caller (``record_read``) owns the ``ServiceNowClient``
    context manager and passes the open client in; this helper never opens or
    closes a client.

    When ``sys_id`` is supplied directly we validate its shape and pass
    through; when ``name`` is supplied we issue a ``limit=2`` lookup so we can
    detect ambiguity at exactly two matches without paging. Error message text
    is byte-identical to the prior inlined version because tests assert on it.
    """
    if sys_id:
        validate_sys_id(sys_id)
        return sys_id, None

    lookup = await client.query_records(
        table,
        ServiceNowQuery().equals("name", name).build(),
        fields=["sys_id"],
        limit=2,
    )
    records = lookup.get("records", [])
    if not records:
        return None, format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"No record found with name={name!r} on table {table!r}.",
        )
    if len(records) > 1:
        return None, format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"Ambiguous name={name!r} on table {table!r}: multiple records match.",
        )
    return records[0]["sys_id"], None


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
