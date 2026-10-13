"""Record selection, mandatory fields, and masked update differences."""

from __future__ import annotations

from typing import Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.records.models import DiffEntry
from servicenow_mcp.platform.client import ServiceNowClient
from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry
from servicenow_mcp.policy import MASK_VALUE, is_sensitive_field
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.validation import validate_sys_id


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


async def _resolve_record_sys_id(
    client: ServiceNowClient,
    table: str,
    sys_id: str,
    name: str,
) -> str:
    """Resolve one record using a bounded lookup that rejects ambiguous names."""
    if sys_id:
        validate_sys_id(sys_id)
        return sys_id

    lookup = await client.query_records(
        table,
        ServiceNowQuery().equals("name", name).build(),
        fields=["sys_id"],
        limit=2,
    )
    records = lookup.get("records", [])
    if not records:
        raise CapabilityError(f"No record found with name={name!r} on table {table!r}.")
    if len(records) > 1:
        raise CapabilityError(f"Ambiguous name={name!r} on table {table!r}: multiple records match.")
    resolved_sys_id = records[0].get("sys_id")
    if not isinstance(resolved_sys_id, str):
        raise CapabilityError(f"Record lookup on table {table!r} did not return a string sys_id.")
    validate_sys_id(resolved_sys_id)
    return resolved_sys_id


def _build_update_diff(
    changes_dict: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, DiffEntry]:
    """Build a field-level diff for a preview update."""
    diff: dict[str, DiffEntry] = {}
    for field, new_value in changes_dict.items():
        if is_sensitive_field(field):
            diff[field] = DiffEntry(old=MASK_VALUE, new=MASK_VALUE)
        else:
            diff[field] = DiffEntry(old=current.get(field, ""), new=new_value)
    return diff
