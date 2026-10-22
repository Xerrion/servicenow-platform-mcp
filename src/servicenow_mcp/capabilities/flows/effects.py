"""Explicit one-shot lifecycle effects, separated from read-only local validation.

Studio 27: publishFlowToServer:97561-97577; saveFlowSaga:99271-99297;
showMessage:31224-31247 distinguishes subflow publication from flow activation.
sendTestToServer:448105-448118 and test success context:448297-448301.
"""

import copy
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import (
    capture,
    data,
    editing,
    lifecycle,
    semantics,
    tree,
)
from servicenow_mcp.capabilities.mutations.models import FlowCommand, fingerprint
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.validation import validate_identifier, validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient

OPS = {"activate", "publish", "deactivate", "test"}
LIFECYCLE_FIELDS = {"active", "status", "isPublished", "masterSnapshotId"}


def _draft_view(flow: dict[str, Any]) -> dict[str, Any]:
    """Only lifecycle-owned state differs for structural semantic comparison."""
    return {
        **copy.deepcopy(flow),
        "active": False,
        "status": "draft",
        "isPublished": False,
        "masterSnapshotId": "",
    }


def _test_inputs(command: FlowCommand, flow: dict[str, Any]) -> dict[str, Any]:
    provided = command.inputs or {}
    if flow["type"] == "subflow":
        parameters = flow["inputs"]
    else:
        triggers = flow["triggerInstances"]
        if len(triggers) != 1 or triggers[0]["type"] not in {
            "service_catalog",
            "record_create",
            "record_update",
            "record_create_or_update",
            "daily",
            "weekly",
            "monthly",
            "run_once",
            "repeat",
        }:
            raise CapabilityError(
                "Test input mapping requires a supported classic trigger or triggerless subflow.",
                "UNAVAILABLE",
            )
        parameters = triggers[0]["outputs"]
    unknown = set(provided) - {row["name"] for row in parameters}
    if unknown:
        raise CapabilityError("Test inputs contain an undeclared parameter.")
    result = {}
    for row in parameters:
        spec = data.spec(row)
        name = row["name"]
        if spec.get("attributes", {}).get("test_input_hidden") == "true":
            if name in provided:
                raise CapabilityError("Hidden test inputs cannot be overridden.")
            continue
        if (
            name in provided
            and spec.get("attributes", {}).get("test_input_read_only") == "true"
        ):
            raise CapabilityError("Read-only test inputs cannot be overridden.")
        link = spec.get("internal_link")
        linked = (
            next(
                (p for p in flow["triggerInstances"][0]["inputs"] if p["name"] == link),
                None,
            )
            if flow["type"] == "flow"
            else None
        )
        kind = data.value_type(row).kind
        if linked and kind == "table_name":
            if name in provided and provided[name] != linked["value"]:
                raise CapabilityError(
                    "The test table must match the configured trigger table."
                )
            result[name] = linked["value"]
            continue
        if (
            kind in {"reference", "document_id"}
            and flow["type"] == "flow"
            and name not in provided
        ):
            raise CapabilityError(
                "Record-trigger tests require an explicit triggering record sys_id."
            )
        if name not in provided:
            if spec.get("mandatory"):
                raise CapabilityError("A required test input is missing.")
            if flow["type"] == "subflow":
                raise CapabilityError(
                    "Supply every scalar subflow test input explicitly, including optional inputs. No test default is selected implicitly."
                )
            continue
        value = provided[name]
        if flow["type"] == "flow" and kind in {"reference", "document_id"}:
            if not isinstance(value, str):
                raise CapabilityError(
                    "Trigger test records require a native sys_id string."
                )
            validate_sys_id(value)
        if (
            tree.pills(value)
            or isinstance(value, dict | list)
            or isinstance(value, str)
            and ("{{" in value or "}}" in value)
        ):
            raise CapabilityError("Test inputs require static scalar native values.")
        probe = {"name": name, "parameter": {**copy.deepcopy(spec), "readonly": False}}
        if (
            spec.get("children")
            or data.value_type(row).kind in {"object", "glide_list", "records"}
            or data.value_type(row).kind.startswith("array.")
        ):
            raise CapabilityError(
                "Complex/list test input serialization requires another adapter.",
                "UNAVAILABLE",
            )
        data.set_value(probe, value)
        if kind in {"reference", "document_id"}:
            table = linked["value"] if linked else spec.get("reference")
            if not isinstance(table, str) or not table:
                raise CapabilityError(
                    "The test record's table is unavailable.", "UNAVAILABLE"
                )
            check_table_access(table)
            validate_identifier(table)
        result[name] = value
    return result


async def state(command: FlowCommand, client: ServiceNowClient) -> dict[str, Any]:
    check_table_access("sys_hub_flow")
    before = await client.flow_record(
        "sys_hub_flow", command.sys_id or "", lifecycle.HEADER_FIELDS
    )
    flow = await client.studio_request(f"flow/{command.sys_id}")
    if (
        flow.get("id") != command.sys_id
        or flow.get("scope") != command.expected_scope
        or flow.get("security", {}).get("can_write") is not True
    ):
        raise CapabilityError(
            "The lifecycle target identity, scope, or write access differs from the bound draft."
        )
    if (
        capture.scalar(before.get("sys_mod_count")) != command.expected_revision
        or capture.scalar(before.get("sys_scope")) != command.expected_scope
    ):
        raise CapabilityError("The lifecycle target revision changed.", "STALE_PREVIEW")
    if any(
        capture.scalar(before.get(field)) is None for field in lifecycle.HEADER_FIELDS
    ):
        raise CapabilityError("Lifecycle header evidence is incomplete.", "UNAVAILABLE")
    validation = None
    if command.action == "deactivate":
        if (
            flow.get("type") != "flow"
            or flow.get("active") is not True
            or flow.get("status") != "published"
            or flow.get("isPublished") is not True
            or not isinstance(flow.get("masterSnapshotId"), str)
            or capture.scalar(before["active"]) != "true"
        ):
            raise CapabilityError(
                "deactivate requires an active flow. Subflow unpublication is not deactivation."
            )
        try:
            semantics.studio_semantics(_draft_view(flow))
        except semantics.IncompleteSemantics as exc:
            raise CapabilityError(
                f"Lifecycle preflight rejected {exc.path}: {exc}. No lifecycle request was sent.",
                "UNAVAILABLE",
            ) from None
        validate_sys_id(flow["masterSnapshotId"])
        if (
            capture.scalar(before["master_snapshot"]) != flow["masterSnapshotId"]
            or capture.scalar(before["status"]) != flow["status"]
        ):
            raise CapabilityError(
                "The published Studio definition and lifecycle header disagree.",
                "UNAVAILABLE",
            )
    else:
        validation_flow = flow
        republish = (
            command.action in {"activate", "publish"}
            and flow.get("active") is False
            and flow.get("isPublished") is True
            and flow.get("status") in {"draft", "published"}
            and isinstance(flow.get("masterSnapshotId"), str)
            and bool(flow["masterSnapshotId"])
        )
        if republish:
            validate_sys_id(flow["masterSnapshotId"])
            validation_flow = _draft_view(flow)
            if capture.scalar(before["master_snapshot"]) != flow["masterSnapshotId"]:
                raise CapabilityError(
                    "The previous published version differs from the lifecycle header.",
                    "UNAVAILABLE",
                )
        editing.require_editable(validation_flow, command.sys_id or "")
        if (
            capture.scalar(before["active"]) != "false"
            or capture.scalar(before["status"]) != flow["status"]
            or capture.scalar(before["type"]) != flow["type"]
        ):
            raise CapabilityError(
                "The Studio definition and lifecycle header disagree.", "UNAVAILABLE"
            )
        if command.action == "publish" and flow["type"] != "subflow":
            raise CapabilityError(
                "Flow snapshot publication also activates the flow. Use activate explicitly; publish is subflow-only.",
                "UNAVAILABLE",
            )
        if command.action == "activate" and flow["type"] != "flow":
            raise CapabilityError("Subflows use publish, not flow activation.")
        validation = await lifecycle.validate_definition(validation_flow, client)
        if validation["valid"] is not True or validation["complete"] is not True:
            raise CapabilityError(
                "Lifecycle effect requires complete local dependency and semantic validation. Inspect flow.validate diagnostics.",
                "UNAVAILABLE",
            )
    inputs = _test_inputs(command, flow) if command.action == "test" else None
    records: dict[str, Any] = {}
    if inputs is not None:
        parameters = (
            flow["inputs"]
            if flow["type"] == "subflow"
            else flow["triggerInstances"][0]["outputs"]
        )
        for row in parameters:
            name = row["name"]
            spec = data.spec(row)
            if (
                name not in inputs
                or data.value_type(row).kind not in {"reference", "document_id"}
                or not inputs[name]
            ):
                continue
            table = spec.get("reference")
            if not table and flow["type"] == "flow":
                table = data.named(
                    flow["triggerInstances"][0]["inputs"],
                    spec.get("internal_link") or "",
                )["value"]
            if not isinstance(table, str) or not table:
                raise CapabilityError(
                    "The selected test record table is unavailable.", "UNAVAILABLE"
                )
            check_table_access(table)
            record = await client.flow_test_record(table, inputs[name])
            if (
                capture.scalar(record.get("sys_id")) != inputs[name]
                or not (capture.scalar(record.get("sys_mod_count")) or "").isdecimal()
            ):
                raise CapabilityError(
                    "The selected test record is missing, unreadable, or has no stable revision.",
                    "UNAVAILABLE",
                )
            records[name] = record
    if before != await client.flow_record(
        "sys_hub_flow", command.sys_id or "", lifecycle.HEADER_FIELDS
    ):
        raise CapabilityError(
            "The flow changed during lifecycle preview.", "STALE_PREVIEW"
        )
    return {
        "flow": flow,
        "header": before,
        "validation": validation,
        "inputs": inputs,
        "test_records": records,
    }


async def preview(
    command: FlowCommand, client: ServiceNowClient
) -> tuple[dict[str, Any], str]:
    current = await state(command, client)
    return {
        "sys_id": command.sys_id,
        "name": current["flow"]["name"],
        "type": current["flow"]["type"],
        "effect": {
            "activate": "publish_snapshot_and_activate_triggers",
            "publish": "publish_callable_subflow_and_update_parent_versions",
            "deactivate": "disable_new_trigger_executions_not_cancel_existing_contexts",
            "test": "execute_draft_with_real_action_side_effects",
        }[command.action],
        "input_names": sorted((current["inputs"] or {}).keys()),
        "test_inputs": mask_sensitive_fields(current["inputs"] or {}),
        "revision": command.expected_revision,
        "verification": "source_derived_offline_only",
        "automatic_replay": False,
    }, fingerprint(current)


def _compare_content(before: dict[str, Any], after: dict[str, Any]) -> None:
    # Lifecycle flags are compared separately, never erased from echo/GET checks.
    semantics.compare_saved(
        _draft_view(before),
        _draft_view(after),
        generated_ids=set(),
        allow_updated_advance=True,
    )


async def apply(
    command: FlowCommand,
    expected: str | None,
    client: ServiceNowClient,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    current = await state(command, client)
    if fingerprint(current) != expected:
        raise CapabilityError(
            "The lifecycle target or dependency changed. Create a new preview.",
            "STALE_PREVIEW",
        )
    await capture.require_current(command, client, context)
    flow = current["flow"]
    operation = (
        "test"
        if command.action == "test"
        else "deactivate"
        if command.action == "deactivate"
        else "subflow"
        if flow["type"] == "subflow"
        else "snapshot"
    )
    payload = (
        {"flow": flow, "outputMap": current["inputs"], "runOnThread": False}
        if command.action == "test"
        else None
        if operation == "deactivate"
        else flow
    )
    accepted_context: str | None = None
    try:
        echo = await client.studio_lifecycle(flow["id"], operation, payload)
        if command.action == "test":
            validate_sys_id(echo)
            accepted_context = echo
            check_table_access("sys_flow_context")
            execution = await client.flow_record(
                "sys_flow_context", echo, lifecycle.EXECUTION_FIELDS
            )
            if (
                capture.scalar(execution.get("sys_id")) != echo
                or capture.scalar(execution.get("state"))
                not in lifecycle.EXECUTION_STATES
            ):
                raise ValueError("Unrelated or incomplete test context")
            context_flow = capture.scalar(execution.get("flow"))
            if context_flow != flow["id"]:
                validate_sys_id(context_flow or "")
                check_table_access("sys_hub_flow_snapshot")
                version = await client.studio_request(
                    f"flow/{context_flow}", minimal=True
                )
                if (
                    version.get("id") != context_flow
                    or version.get("parentFlow") != flow["id"]
                    or version.get("snapshot") is not True
                ):
                    raise ValueError("Unrelated test snapshot")
            actual = await client.studio_request(f"flow/{flow['id']}")
            semantics.compare_saved(flow, actual, generated_ids=set())
            if current["header"] != await client.flow_record(
                "sys_hub_flow", flow["id"], lifecycle.HEADER_FIELDS
            ):
                raise ValueError("Test changed draft state")
            await capture.require_current(command, client, context)
            return {
                "action": "test",
                "sys_id": flow["id"],
                "execution_sys_id": echo,
                "state": capture.scalar(execution["state"]),
                "terminal": capture.scalar(execution["state"])
                in {"COMPLETE", "ERROR", "CANCELLED"},
                "execution_succeeded": (
                    capture.scalar(execution["state"]) == "COMPLETE"
                )
                if capture.scalar(execution["state"])
                in {"COMPLETE", "ERROR", "CANCELLED"}
                else None,
                "follow_up": "Use flow.status with this exact execution_sys_id. Do not replay the test.",
                "capture": "not_asserted_for_execution",
                "version_content_verified": False,
            }
        if not isinstance(echo, dict) or echo.get("id") != flow["id"]:
            raise ValueError("Lifecycle identity")
        actual = await client.studio_request(f"flow/{flow['id']}")
        _compare_content(flow, echo)
        _compare_content(flow, actual)
        semantics.compare_saved(
            _draft_view(echo), _draft_view(actual), generated_ids=set()
        )
        if any(
            echo.get(k) != actual.get(k) or type(echo.get(k)) is not type(actual.get(k))
            for k in LIFECYCLE_FIELDS
        ):
            raise ValueError("Lifecycle echo drift")
        active = command.action != "deactivate"
        if actual.get("active") is not active:
            raise ValueError("Unexpected activation state")
        if command.action != "deactivate" and (
            actual.get("status") != "published" or actual.get("isPublished") is not True
        ):
            raise ValueError("Publication not established")
        header = await client.flow_record(
            "sys_hub_flow", flow["id"], lifecycle.HEADER_FIELDS
        )
        if (
            capture.scalar(header.get("sys_id")) != flow["id"]
            or capture.scalar(header.get("active")) != str(active).lower()
            or capture.scalar(header.get("sys_scope")) != command.expected_scope
            or capture.scalar(header.get("type")) != flow["type"]
            or capture.scalar(header.get("status")) != actual.get("status")
            or capture.scalar(header.get("master_snapshot"))
            != actual.get("masterSnapshotId")
        ):
            raise ValueError("Independent lifecycle header mismatch")
        if command.action != "deactivate":
            snapshot = actual.get("masterSnapshotId")
            if not isinstance(snapshot, str):
                raise ValueError("Missing snapshot identity")
            validate_sys_id(snapshot)
            if (
                capture.scalar(header.get("master_snapshot")) != snapshot
                or capture.scalar(header.get("status")) != "published"
            ):
                raise ValueError("Snapshot header mismatch")
            check_table_access("sys_hub_flow_snapshot")
            published = await client.studio_request(f"flow/{snapshot}", minimal=True)
            if (
                published.get("id") != snapshot
                or published.get("parentFlow") != flow["id"]
                or published.get("snapshot") is not True
                or published.get("masterSnapshot") is not True
                or published.get("type") != flow["type"]
            ):
                raise ValueError("Snapshot ownership mismatch")
        elif any(
            actual.get(k) != flow.get(k)
            for k in ("status", "isPublished", "masterSnapshotId")
        ):
            raise ValueError("Deactivation changed publication version")
    except Exception:  # noqa: BLE001 - contain ambiguous effects without exposing payloads or retrying
        raise CapabilityError(
            "Lifecycle result or independent readback is incomplete. UNKNOWN_OUTCOME: inspect flow.status before another preview. "
            + (
                f"Returned execution_sys_id={accepted_context}. "
                if accepted_context
                else ""
            )
            + "Do not replay the effect.",
            "UNKNOWN_OUTCOME",
        ) from None
    return {
        "action": command.action,
        "sys_id": flow["id"],
        "type": flow["type"],
        "active": active,
        "status": actual["status"],
        "snapshot_sys_id": actual.get("masterSnapshotId"),
        "snapshot_content_verified": False,
        "execution_requested_directly": False,
        "triggers_enabled": active if flow["type"] == "flow" else None,
    }
