"""Flow target identity, application scope, and revision checks. No session selection."""

from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.mutations.models import FlowCommand, fingerprint
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient

HEADER_FIELDS = ["sys_id", "sys_scope", "sys_updated_on", "sys_mod_count"]


def scalar(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("value")
    return value if isinstance(value, str) else None


async def read_header(client: ServiceNowClient, sys_id: str) -> dict[str, Any]:
    check_table_access("sys_hub_flow")
    header = await client.flow_record("sys_hub_flow", sys_id, HEADER_FIELDS)
    revision, scope = (
        scalar(header.get("sys_mod_count")),
        scalar(header.get("sys_scope")),
    )
    if (
        scalar(header.get("sys_id")) != sys_id
        or not scope
        or not revision
        or not revision.isascii()
        or not revision.isdecimal()
        or not scalar(header.get("sys_updated_on"))
    ):
        raise CapabilityError(
            "The flow target scope or revision is incomplete.", "UNAVAILABLE"
        )
    if scope != "global":
        validate_sys_id(scope)
    return header


async def read_binding(
    command: FlowCommand, client: ServiceNowClient
) -> dict[str, Any] | None:
    if not command.sys_id:
        if (
            command.expected_scope is not None
            and command.scope != command.expected_scope
        ):
            raise CapabilityError(
                "The creation scope differs from expected_scope.", "CONTEXT_MISMATCH"
            )
        return {"scope": command.scope} if command.expected_scope is not None else None
    if command.expected_scope is None and command.expected_revision is None:
        return None
    header = await read_header(client, command.sys_id)
    if (
        command.expected_scope is not None
        and scalar(header["sys_scope"]) != command.expected_scope
    ):
        raise CapabilityError(
            "The target application scope differs from expected_scope.",
            "CONTEXT_MISMATCH",
        )
    if (
        command.expected_revision is not None
        and scalar(header["sys_mod_count"]) != command.expected_revision
    ):
        raise CapabilityError("The target flow revision changed.", "STALE_PREVIEW")
    return {"header": header}


async def require_current(
    command: FlowCommand, client: ServiceNowClient, before: dict[str, Any] | None
) -> None:
    if before is not None and fingerprint(
        await read_binding(command, client)
    ) != fingerprint(before):
        raise CapabilityError(
            "The target scope or revision changed before the write. Create a new preview.",
            "STALE_PREVIEW",
        )


async def verify_after(
    command: FlowCommand,
    client: ServiceNowClient,
    sys_id: str,
    before: dict[str, Any] | None,
) -> str | None:
    if before is None:
        return None
    try:
        header = await read_header(client, sys_id)
        scope = command.expected_scope or scalar(
            before.get("header", {}).get("sys_scope")
        )
        if scalar(header["sys_scope"]) != scope:
            raise ValueError("Target scope changed")
        previous = before.get("header")
        if (
            previous is not None
            and int(scalar(header["sys_mod_count"]) or "-1")
            != int(scalar(previous["sys_mod_count"]) or "-1") + 1
        ):
            raise ValueError("Target revision did not advance exactly once")
        if await read_header(client, sys_id) != header:
            raise ValueError("Target changed during readback")
        return scalar(header["sys_mod_count"])
    except Exception:  # noqa: BLE001 - a write succeeded but persisted target verification is incomplete
        raise CapabilityError(
            "The write returned, but the target scope or revision could not be verified. Inspect the flow. Do not replay the write.",
            "UNKNOWN_OUTCOME",
        ) from None
