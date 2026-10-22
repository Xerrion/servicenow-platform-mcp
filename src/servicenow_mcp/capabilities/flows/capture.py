"""Explicit application/update-set binding and bounded metadata capture verification."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.mutations.models import FlowCommand, fingerprint
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient


CAPTURE_FIELDS = (
    "sys_id,name,update_set,application,sys_updated_on,sys_mod_count,action"
)
HEADER_FIELDS = ["sys_id", "sys_scope", "sys_updated_on", "sys_mod_count"]


def timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def scalar(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("value")
    return value if isinstance(value, str) else None


async def _capture_candidates(
    client: ServiceNowClient,
    sys_id: str,
    *,
    expected_update_set: str | None = None,
    expected_scope: str | None = None,
) -> list[dict[str, Any]]:
    validate_sys_id(sys_id)
    check_table_access("sys_update_xml")
    selection = (expected_update_set, expected_scope)
    bound = any(value is not None for value in selection)
    if bound and any(value is None for value in selection):
        raise CapabilityError(
            "Capture selection requires scope and update set.", "UNAVAILABLE"
        )
    rows = await client.flow_capture_rows(
        sys_id,
        CAPTURE_FIELDS,
        update_set=expected_update_set,
        scope=expected_scope,
    )
    if not isinstance(rows, list) or len(rows) >= (101 if bound else 2):
        raise CapabilityError(
            "Customer Update metadata is ambiguous or incomplete.", "UNAVAILABLE"
        )
    candidates = []
    identities = set()
    for row in rows:
        fields = CAPTURE_FIELDS.split(",")
        if not isinstance(row, dict) or any(scalar(row.get(k)) is None for k in fields):
            raise CapabilityError(
                "Customer Update fields are incomplete.", "UNAVAILABLE"
            )
        result: dict[str, Any] = {k: scalar(row[k]) for k in fields}
        if result["name"] != f"sys_hub_flow_{sys_id}":
            raise CapabilityError(
                "Customer Update identity does not match the target flow.",
                "UNAVAILABLE",
            )
        validate_sys_id(result["sys_id"] or "")
        validate_sys_id(result["update_set"] or "")
        if result["application"] != "global":
            validate_sys_id(result["application"] or "")
        if (
            not result["sys_mod_count"]
            or not result["sys_mod_count"].isascii()
            or not result["sys_mod_count"].isdecimal()
        ):
            raise CapabilityError(
                "Customer Update revision is malformed.", "UNAVAILABLE"
            )
        timestamp(result["sys_updated_on"])
        if result["sys_id"] in identities:
            raise CapabilityError(
                "Customer Update identities are ambiguous.", "UNAVAILABLE"
            )
        identities.add(result["sys_id"])
        if bound and (
            result["update_set"] != expected_update_set
            or result["application"] != expected_scope
        ):
            raise CapabilityError(
                "Customer Update context differs from the bound target.", "UNAVAILABLE"
            )
        candidates.append(result)
    return sorted(candidates, key=lambda row: row["sys_id"])


def _latest_capture(
    candidates: list[dict[str, Any]], minimum_updated_on: str | None
) -> dict[str, Any] | None:
    if not candidates:
        return None
    minimum = timestamp(minimum_updated_on) if minimum_updated_on is not None else None
    newest = max(timestamp(row["sys_updated_on"]) for row in candidates)
    latest = [row for row in candidates if timestamp(row["sys_updated_on"]) == newest]
    if len(latest) != 1:
        raise CapabilityError(
            "Customer Update metadata has tied latest revisions.", "UNAVAILABLE"
        )
    if minimum is not None and newest < minimum:
        raise CapabilityError(
            "Customer Update metadata predates the flow revision.", "UNAVAILABLE"
        )
    return latest[0]


async def read_capture(
    client: ServiceNowClient,
    sys_id: str,
    *,
    expected_update_set: str | None = None,
    expected_scope: str | None = None,
    minimum_updated_on: str | None = None,
) -> dict[str, Any] | None:
    selection = (expected_update_set, expected_scope, minimum_updated_on)
    if any(value is not None for value in selection) and any(
        value is None for value in selection
    ):
        raise CapabilityError(
            "Capture selection requires scope, update set, and flow revision time.",
            "UNAVAILABLE",
        )
    candidates = await _capture_candidates(
        client,
        sys_id,
        expected_update_set=expected_update_set,
        expected_scope=expected_scope,
    )
    return _latest_capture(candidates, minimum_updated_on)


async def read_binding(
    command: FlowCommand, client: ServiceNowClient
) -> dict[str, Any] | None:
    if command.expected_scope is None:
        return None
    for table in ("sys_scope", "sys_update_set", "sys_hub_flow"):
        check_table_access(table)
    try:
        session = await client.flow_session_context()
        if session != {
            "scope": command.expected_scope,
            "update_set": command.expected_update_set,
        }:
            raise CapabilityError(
                "The current application or update set differs from the expected context. No session switch is "
                "allowed.",
                "CONTEXT_MISMATCH",
            )
        update_set = await client.flow_record(
            "sys_update_set",
            command.expected_update_set or "",
            ["sys_id", "application", "state", "sys_updated_on"],
        )
        if (
            scalar(update_set.get("sys_id")) != command.expected_update_set
            or scalar(update_set.get("application")) != command.expected_scope
            or scalar(update_set.get("state")) != "in progress"
            or not scalar(update_set.get("sys_updated_on"))
        ):
            raise CapabilityError(
                "The expected update set is unreadable, not in progress, or belongs to another application.",
                "CONTEXT_MISMATCH",
            )
        header = None
        captures = []
        if command.sys_id:
            header = await client.flow_record(
                "sys_hub_flow", command.sys_id, HEADER_FIELDS
            )
            if (
                scalar(header.get("sys_id")) != command.sys_id
                or scalar(header.get("sys_scope")) != command.expected_scope
                or scalar(header.get("sys_mod_count")) != command.expected_revision
            ):
                raise CapabilityError(
                    "The flow scope or revision differs from the expected context.",
                    "STALE_PREVIEW",
                )
            captures = await _capture_candidates(
                client,
                command.sys_id,
                expected_update_set=command.expected_update_set,
                expected_scope=command.expected_scope,
            )
            _latest_capture(captures, scalar(header["sys_updated_on"]))
        elif command.scope != command.expected_scope:
            raise CapabilityError(
                "The creation scope differs from the expected application.",
                "CONTEXT_MISMATCH",
            )
        return {
            "session": session,
            "update_set": update_set,
            "header": header,
            "captures": captures,
        }
    except CapabilityError:
        raise
    except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
        raise CapabilityError(
            "Current application/update-set context could not be established through the authenticated read "
            "contract. No write is allowed.",
            "UNAVAILABLE",
        ) from None


async def verify_after(
    command: FlowCommand,
    client: ServiceNowClient,
    sys_id: str,
    before: dict[str, Any] | None,
) -> dict[str, Any]:
    if before is None:
        return {
            "state": "unverified",
            "scope": "not_bound",
            "reason": "No explicit application/update-set binding was supplied. The draft save does not prove capture.",
        }
    try:
        session = await client.flow_session_context()
        if session != before["session"]:
            raise ValueError("Session changed")
        header = await client.flow_record("sys_hub_flow", sys_id, HEADER_FIELDS)
        if any(scalar(header.get(field)) is None for field in HEADER_FIELDS):
            raise ValueError("Incomplete flow capture revision")
        if (
            scalar(header.get("sys_scope")) != command.expected_scope
            or scalar(header.get("sys_id")) != sys_id
        ):
            raise ValueError("Flow scope changed")
        update_set = await client.flow_record(
            "sys_update_set",
            command.expected_update_set or "",
            ["sys_id", "application", "state", "sys_updated_on"],
        )
        if (
            scalar(update_set.get("sys_id")) != command.expected_update_set
            or scalar(update_set.get("application")) != command.expected_scope
            or scalar(update_set.get("state")) != "in progress"
        ):
            raise ValueError("Update set changed")
        candidates = await _capture_candidates(
            client,
            sys_id,
            expected_update_set=command.expected_update_set,
            expected_scope=command.expected_scope,
        )
        latest = _latest_capture(candidates, scalar(header["sys_updated_on"]))
        old = {row["sys_id"]: row for row in before["captures"]}
        current = {row["sys_id"]: row for row in candidates}
        if old.keys() - current.keys():
            raise ValueError("Capture candidates disappeared")
        changed = [row for row in candidates if old.get(row["sys_id"]) != row]
        if len(changed) != 1 or changed[0] != latest:
            raise ValueError("Capture changes are ambiguous or stale")
        after = changed[0]
        previous = old.get(after["sys_id"])
        if previous is None:
            if int(after["sys_mod_count"]) != 0:
                raise ValueError("New capture has intervening revisions")
        else:
            if int(after["sys_mod_count"]) != int(previous["sys_mod_count"]) + 1:
                raise ValueError("Capture revision did not advance exactly once")
            if timestamp(after["sys_updated_on"]) < timestamp(
                previous["sys_updated_on"]
            ):
                raise ValueError("Capture timestamp regressed")
        if (
            after is None
            or after["update_set"] != command.expected_update_set
            or after["application"] != command.expected_scope
            or after["action"] != "INSERT_OR_UPDATE"
        ):
            raise ValueError("Capture mismatch")
        updated = scalar(header.get("sys_updated_on"))
        if (
            not updated
            or not after["sys_updated_on"]
            or timestamp(after["sys_updated_on"]) < timestamp(updated)
        ):
            raise ValueError("Capture predates save")
        # Multiple intervening updates cannot be attributed to this one authorized save.
        if (
            before["header"] is not None
            and int(scalar(header.get("sys_mod_count")) or "-1")
            != int(command.expected_revision or "-1") + 1
        ):
            raise ValueError("Flow revision did not advance")
        final_header = await client.flow_record("sys_hub_flow", sys_id, HEADER_FIELDS)
        if final_header != header:
            raise ValueError("Flow changed during capture readback")
        if await client.flow_session_context() != before["session"]:
            raise ValueError("Session changed during capture readback")
        return {
            "state": "metadata_verified",
            "scope": "flow_header_customer_update",
            "update_set": command.expected_update_set,
            "application": command.expected_scope,
            "customer_update_sys_id": after["sys_id"],
            "limitation": (
                "This verifies header capture metadata only. Child artifacts and Customer Update payload content "
                "are not verified."
            ),
        }
    except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
        raise CapabilityError(
            "The draft was saved, but application/update-set context or fresh capture metadata could not be "
            "verified. UNKNOWN_OUTCOME: inspect flow.status and Customer Updates before another preview. Do not "
            "replay the write.",
            "UNKNOWN_OUTCOME",
        ) from None


async def require_current(
    command: FlowCommand, client: ServiceNowClient, before: dict[str, Any] | None
) -> None:
    if before is not None and fingerprint(
        await read_binding(command, client)
    ) != fingerprint(before):
        raise CapabilityError(
            "Application/update-set context or capture metadata changed before save. Create a new preview.",
            "STALE_PREVIEW",
        )
