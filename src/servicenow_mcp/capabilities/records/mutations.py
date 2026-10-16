"""Record mutation previews, dictionary checks, and one-request execution."""

from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.mutations.models import RecordCommand, fingerprint
from servicenow_mcp.capabilities.records._artifact import validate_ui_macro_xml
from servicenow_mcp.capabilities.records._record_helpers import (
    _build_update_diff,
    _check_mandatory_fields,
)
from servicenow_mcp.policy import mask_record, mask_sensitive_fields

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient
    from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry


async def validate_metadata(
    command: RecordCommand, client: ServiceNowClient, dictionary: DictionaryRegistry
) -> None:
    if command.data is None:
        return
    fields = await dictionary.get_fields(command.table, list(command.data))
    for field in fields:
        if field.internal_type == "xml":
            content = command.data[field.name]
            if not isinstance(content, str):
                raise CapabilityError(
                    f"XML field {field.name!r} requires a string value."
                )
            error = validate_ui_macro_xml(content)
            if error:
                raise CapabilityError(f"Field {field.name!r}: {error}")
    if command.action == "create":
        missing = await _check_mandatory_fields(
            client, command.table, command.data, dictionary
        )
        if missing:
            raise CapabilityError(
                f"Missing mandatory fields for table '{command.table}': {', '.join(missing)}",
                "MISSING_MANDATORY_FIELDS",
                missing_fields=missing,
            )


async def preview(
    command: RecordCommand,
    client: ServiceNowClient,
    dictionary: DictionaryRegistry,
) -> tuple[dict[str, Any], str | None]:
    await validate_metadata(command, client, dictionary)
    summary = {"table": command.table, "sys_id": command.sys_id}
    if command.action == "create":
        summary["data"] = mask_sensitive_fields(command.data or {})
        return summary, None
    current = await client.get_record(command.table, command.sys_id or "")
    if command.action == "update":
        summary["diff"] = {
            name: entry.to_payload()
            for name, entry in _build_update_diff(command.data or {}, current).items()
        }
    else:
        summary["record_snapshot"] = mask_record(command.table, current)
    return summary, fingerprint(current)


async def apply(
    command: RecordCommand,
    expected: str | None,
    client: ServiceNowClient,
    dictionary: DictionaryRegistry,
) -> dict[str, Any]:
    dictionary.flush(command.table)
    await validate_metadata(command, client, dictionary)
    if command.action != "create":
        current = await client.get_record(command.table, command.sys_id or "")
        if fingerprint(current) != expected:
            raise CapabilityError(
                "The record changed since preview. Create a new preview.",
                "STALE_PREVIEW",
            )
    if command.action == "delete":
        await client.delete_record(command.table, command.sys_id or "")
        return {
            "action": command.action,
            "table": command.table,
            "sys_id": command.sys_id,
            "deleted": True,
        }
    if command.action == "create":
        record = await client.create_record(command.table, command.data or {})
    else:
        record = await client.update_record(
            command.table, command.sys_id or "", command.data or {}
        )
    if not isinstance(record, dict) or not record.get("sys_id"):
        raise CapabilityError(
            "Mutation response was incomplete. Remote outcome is unknown. Check the record before another preview.",
            "UNKNOWN_OUTCOME",
        )
    return {
        "action": command.action,
        "table": command.table,
        "sys_id": record["sys_id"],
        "record": mask_record(command.table, record),
    }
