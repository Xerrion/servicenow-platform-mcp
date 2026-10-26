"""Read existing Flow lifecycle state and execution evidence without starting work."""

import re
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient


def scalar(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("value")
    return value if isinstance(value, str) else None


HEADER_FIELDS = [
    "sys_id",
    "type",
    "sys_scope",
    "active",
    "status",
    "master_snapshot",
    "latest_snapshot",
    "sys_updated_on",
    "sys_mod_count",
]


EXECUTION_FIELDS = [
    "sys_id",
    "flow",
    "snapshot",
    "state",
    "sys_created_on",
    "sys_updated_on",
]


EXECUTION_STATES = {
    "ERROR",
    "CONTINUE_SYNC",
    "PAUSED",
    "CANCELLED",
    "WAITING",
    "IN_PROGRESS",
    "PRESUMED_INTERRUPTED",
    "COMPLETE",
    "QUEUED",
    "PAUSED_IN_DEBUG",
}


def diagnostic(
    code: str, path: str, message: str, *, incomplete: bool = False
) -> dict[str, Any]:
    return {"code": code, "path": path, "message": message, "incomplete": incomplete}


async def execution_snapshot(
    context: dict[str, Any], sys_id: str, client: ServiceNowClient
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Verify the exact compiled snapshot's owner, never its authored content or version."""
    evidence: dict[str, Any] = {
        "snapshot_sys_id": None,
        "snapshot_owner_verified": False,
        "snapshot_content_verified": False,
        "version_verified": False,
    }
    snapshot_id = scalar(context.get("snapshot"))
    if context.get("snapshot") is None:
        code = "MISSING_EXECUTION_SNAPSHOT"
        message = "The execution snapshot reference is unavailable; snapshot ownership is unverified."
    elif snapshot_id == "":
        code = "PENDING_EXECUTION_SNAPSHOT"
        message = "The execution has not yet reported a compiled snapshot. Do not replay the test."
    else:
        if snapshot_id is None or re.fullmatch(r"[0-9a-f]{32}", snapshot_id) is None:
            code = "MALFORMED_EXECUTION_SNAPSHOT"
            message = "The execution snapshot reference is malformed; snapshot ownership is unverified."
        else:
            evidence["snapshot_sys_id"] = snapshot_id
            try:
                check_table_access("sys_hub_snapshot")
                snapshot = await client.get_record(
                    "sys_hub_snapshot",
                    snapshot_id,
                    fields=["sys_id", "parent", "sys_mod_count", "sys_updated_on"],
                )
            except Exception:  # noqa: BLE001 — an unreadable snapshot does not authorize effect replay
                code = "UNREADABLE_EXECUTION_SNAPSHOT"
                message = "The exact compiled snapshot is unreadable; snapshot ownership is unverified."
            else:
                identity = (
                    scalar(snapshot.get("sys_id"))
                    if isinstance(snapshot, dict)
                    else None
                )
                parent = (
                    scalar(snapshot.get("parent"))
                    if isinstance(snapshot, dict)
                    else None
                )
                if (
                    identity is None
                    or parent is None
                    or re.fullmatch(r"[0-9a-f]{32}", identity) is None
                    or re.fullmatch(r"[0-9a-f]{32}", parent) is None
                ):
                    code = "MALFORMED_EXECUTION_SNAPSHOT"
                    message = "The compiled snapshot identity or parent is incomplete or malformed."
                else:
                    if identity == snapshot_id and parent == sys_id:
                        evidence["snapshot_owner_verified"] = True
                        return evidence, []
                    code = "EXECUTION_SNAPSHOT_MISMATCH"
                    message = "The compiled snapshot identity or parent does not match the exact execution snapshot and requested flow."
    return evidence, [diagnostic(code, "execution.snapshot", message, incomplete=True)]


async def status(
    sys_id: str, execution_sys_id: str | None, client: ServiceNowClient
) -> CapabilityResult:
    validate_sys_id(sys_id)
    if execution_sys_id:
        validate_sys_id(execution_sys_id)
    check_table_access("sys_hub_flow")
    diagnostics: list[dict[str, Any]] = []
    try:
        row = await client.flow_record("sys_hub_flow", sys_id, HEADER_FIELDS)
        if scalar(row.get("sys_id")) != sys_id:
            raise ValueError("Unexpected flow identity")
    except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
        row = {}
        diagnostics.append(
            diagnostic(
                "UNREADABLE_FLOW",
                "flow",
                "Flow lifecycle state is unreadable.",
                incomplete=True,
            ),
        )
    missing = [field for field in HEADER_FIELDS if scalar(row.get(field)) is None]
    if missing:
        diagnostics.append(
            diagnostic(
                "PARTIAL_FLOW",
                "flow",
                "Lifecycle fields are incomplete. Missing: " + ", ".join(missing),
                incomplete=True,
            ),
        )
    revision = scalar(row.get("sys_mod_count"))
    if revision is not None and (not revision.isascii() or not revision.isdecimal()):
        diagnostics.append(
            diagnostic(
                "UNKNOWN_REVISION",
                "flow.revision",
                "The flow revision is malformed.",
                incomplete=True,
            ),
        )
    active = {"true": True, "false": False}.get(scalar(row.get("active")) or "")
    state = scalar(row.get("status"))
    if active is None or state not in {"draft", "published"}:
        diagnostics.append(
            diagnostic(
                "UNKNOWN_STATE",
                "flow",
                "The active flag or authoring status is unknown.",
                incomplete=True,
            ),
        )
    master = scalar(row.get("master_snapshot"))
    latest = scalar(row.get("latest_snapshot"))
    if scalar(row.get("type")) not in {"flow", "subflow"} or (
        state == "published" and not master
    ):
        diagnostics.append(
            diagnostic(
                "INCONSISTENT_LIFECYCLE",
                "flow",
                "Flow type or published snapshot evidence is incomplete.",
                incomplete=True,
            ),
        )
    for identity in (master, latest):
        if identity:
            try:
                validate_sys_id(identity)
            except ValueError:
                diagnostics.append(
                    diagnostic(
                        "MALFORMED_VERSION",
                        "flow",
                        "A snapshot version reference is malformed.",
                        incomplete=True,
                    ),
                )
    execution: dict[str, Any] | None = None
    if execution_sys_id:
        check_table_access("sys_flow_context")
        execution = {
            "sys_id": execution_sys_id,
            "state": None,
            "terminal": None,
            "context_owner_verified": False,
            "snapshot_sys_id": None,
            "snapshot_owner_verified": False,
            "snapshot_content_verified": False,
            "version_verified": False,
        }
        try:
            context = await client.flow_record(
                "sys_flow_context", execution_sys_id, EXECUTION_FIELDS
            )
            if scalar(context.get("sys_id")) != execution_sys_id:
                raise ValueError("Unexpected context identity")
            snapshot_evidence, snapshot_diagnostics = await execution_snapshot(
                context, sys_id, client
            )
            execution.update(snapshot_evidence)
            diagnostics.extend(snapshot_diagnostics)
            if any(
                scalar(context.get(k)) is None
                for k in EXECUTION_FIELDS
                if k != "snapshot"
            ):
                raise ValueError("Incomplete context")
            context_flow = scalar(context["flow"]) or ""
            if context_flow not in {sys_id, master, latest}:
                validate_sys_id(context_flow)
                check_table_access("sys_hub_flow_snapshot")
                ancestor = await client.studio_request(
                    f"flow/{context_flow}", minimal=True
                )
                if (
                    ancestor.get("id") != context_flow
                    or ancestor.get("parentFlow") != sys_id
                    or ancestor.get("snapshot") is not True
                ):
                    raise ValueError("Unrelated context")
            context_state = scalar(context["state"])
            execution.update(
                context_owner_verified=True,
                state=context_state,
                terminal=context_state in {"ERROR", "COMPLETE", "CANCELLED"}
                if context_state in EXECUTION_STATES
                else None,
                sys_updated_on=scalar(context["sys_updated_on"]),
            )
            if context_state not in EXECUTION_STATES:
                raise ValueError("Unknown context state")
        except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
            diagnostics.append(
                diagnostic(
                    "UNKNOWN_EXECUTION",
                    "execution",
                    "Execution state is missing, unrelated, or unknown. This does not authorize replay.",
                    incomplete=True,
                ),
            )
    try:
        after = await client.flow_record("sys_hub_flow", sys_id, HEADER_FIELDS)
        if after != row:
            raise ValueError("Revision changed")
    except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
        diagnostics.append(
            diagnostic(
                "READ_RACE",
                "flow",
                "Flow state changed or could not be rechecked during status assembly.",
                incomplete=True,
            ),
        )
    return CapabilityResult(
        data={
            "complete": not diagnostics,
            "completeness_scope": "flow_lifecycle_and_requested_execution_only",
            "flow": {
                "sys_id": sys_id,
                "scope": scalar(row.get("sys_scope")),
                "active": active,
                "status": state,
                "draft": state == "draft" if state in {"draft", "published"} else None,
                "has_published_snapshot": bool(master) if master is not None else None,
                "published_snapshot": master,
                "latest_snapshot": latest,
                "revision": {
                    "updated_on": scalar(row.get("sys_updated_on")),
                    "mod_count": scalar(row.get("sys_mod_count")),
                },
            },
            "execution": execution,
            "diagnostics": diagnostics,
            "replay_safe": False,
        },
    )
