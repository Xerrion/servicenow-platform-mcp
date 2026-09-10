"""Shared helpers for record-level write operations.

Single source of truth for the mandatory-field validation routine and the
update-diff builder. Imported by both the legacy ``record_write`` tool module
and the unified ``record_write`` / ``record_apply`` tools.
"""

from __future__ import annotations

from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.policy import MASK_VALUE, is_sensitive_field
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.utils import ServiceNowQuery, format_response, validate_sys_id


async def _check_mandatory_fields(
    client: ServiceNowClient,
    table: str,
    data: dict[str, Any],
    dictionary: DictionaryRegistry,
) -> list[str]:
    """Return missing mandatory fields, resolving child declarations first.

    Absent keys, None, and empty strings are missing; false and zero are supplied.
    Metadata errors propagate so a failed lookup cannot bypass write checks.
    """
    chain = await dictionary.get_chain(table)
    mandatory_fields: dict[str, bool] = {}
    for current in chain:
        result = await client.query_records(
            table="sys_dictionary",
            query=ServiceNowQuery().equals("name", current).is_not_empty("element").equals("active", "true").build(),
            fields=["element", "mandatory"],
            limit=1000,
        )
        for entry in result.get("records", []):
            name = str(entry.get("element") or "").strip()
            if not name or name in mandatory_fields:
                continue
            mandatory_fields[name] = entry.get("mandatory") in ("true", True)
    return [name for name, is_mandatory in mandatory_fields.items() if is_mandatory and data.get(name) in (None, "")]


async def _check_mandatory_or_error(
    client: ServiceNowClient,
    table: str,
    data: dict[str, Any],
    dictionary: DictionaryRegistry,
) -> str | None:
    """Check for missing mandatory fields and return error response if any, else None."""
    missing = await _check_mandatory_fields(client, table, data, dictionary)
    if missing:
        return format_response(
            data={"table": table, "missing_fields": missing},
            status="error",
            error=f"Missing mandatory fields for table '{table}': {', '.join(missing)}",
        )
    return None


async def _resolve_record_sys_id(
    client: ServiceNowClient,
    table: str,
    sys_id: str,
    name: str,
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
            status="error",
            error=f"No record found with name={name!r} on table {table!r}.",
        )
    if len(records) > 1:
        return None, format_response(
            data=None,
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
