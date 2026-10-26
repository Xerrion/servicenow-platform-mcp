"""Explicit one-shot lifecycle effects, separated from read-only local validation.

Studio 27: publishFlowToServer:97561-97577; saveFlowSaga:99271-99297;
showMessage:31224-31247 distinguishes subflow publication from flow activation.
sendTestToServer:448105-448118 and test success context:448297-448301.
"""

import copy
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import (
    active_drafts,
    data,
    editing,
    lifecycle,
    native,
    semantics,
    target,
    tree,
)
from servicenow_mcp.capabilities.mutations.models import FlowCommand, fingerprint
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.validation import validate_identifier, validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient

OPS = {"activate", "publish", "deactivate", "test"}
LIFECYCLE_FIELDS = {
    "active",
    "status",
    "isPublished",
    "masterSnapshotId",
    "latestSnapshot",
    "remoteTriggerSysId",
    "version",
}
_TEST_ARRAYS = {
    "array.string": "string",
    "array.integer": "integer",
    "array.boolean": "boolean",
    "array.object": "object",
}
_TEST_COMPLEX = {"object", *_TEST_ARRAYS}


def _draft_view(flow: dict[str, Any]) -> dict[str, Any]:
    """Only lifecycle-owned state differs for structural semantic comparison."""
    result = {
        **copy.deepcopy(flow),
        "active": False,
        "status": "draft",
        "isPublished": False,
        "masterSnapshotId": "",
    }
    for key in ("latestSnapshot", "remoteTriggerSysId", "version"):
        result.pop(key, None)
    return result


def _test_schema(row: dict[str, Any], *, nested: bool = False) -> dict[str, Any]:
    parameter = data.spec(row)
    kind = data.value_type(row).kind
    attributes = parameter.get("attributes", {})
    if not isinstance(attributes, dict):
        raise CapabilityError("Test input attributes are incomplete.", "UNAVAILABLE")
    if any(
        parameter.get(key)
        for key in (
            "dynamic",
            "data_structure",
            "dependent_on",
            "use_dependent",
            "scriptActive",
        )
    ) or row.get("scriptActive"):
        raise CapabilityError(
            "Dynamic, dependent, or scripted subflow test inputs require another adapter.",
            "UNAVAILABLE",
        )
    if kind not in {
        *_TEST_COMPLEX,
        "slushbucket",
        "approval_rules",
        "schedule_date_time",
        "template_value",
        "conditions",
        "boolean",
        "integer",
        "long",
        "string",
        "choice",
        "reference",
        "document_id",
        "table_name",
        "field_name",
        "json",
    }:
        raise CapabilityError("This test input type is unsupported.", "UNAVAILABLE")
    if nested and kind in {
        "reference",
        "document_id",
        "slushbucket",
        "approval_rules",
        "schedule_date_time",
        "template_value",
        "conditions",
    }:
        raise CapabilityError(
            "Nested record references or reference-bearing widgets require independent record binding and are unsupported.",
            "UNAVAILABLE",
        )
    children = data.children(row)
    if kind == "object":
        if not children:
            raise CapabilityError(
                "Object test inputs require a complete declared child schema.",
                "UNAVAILABLE",
            )
    elif kind in _TEST_ARRAYS:
        if (
            len(children) != 1
            or data.value_type(children[0]).kind != _TEST_ARRAYS[kind]
        ):
            raise CapabilityError(
                "Array test inputs require one matching declared item schema.",
                "UNAVAILABLE",
            )
    elif children:
        raise CapabilityError(
            "Scalar test inputs cannot contain child schemas.", "UNAVAILABLE"
        )
    names = [child.get("name") for child in children]
    if any(not isinstance(name, str) or not name.split(".")[-1] for name in names):
        raise CapabilityError("Test input child names are incomplete.", "UNAVAILABLE")
    if len({str(name).split(".")[-1] for name in names}) != len(names):
        raise CapabilityError("Test input child names are ambiguous.", "UNAVAILABLE")
    return {
        **copy.deepcopy(parameter),
        "name": row["name"],
        "type": kind,
        "children": [_test_schema(child, nested=True) for child in children],
    }


def _validate_test_value(schema: dict[str, Any], value: Any) -> None:
    attributes = schema.get("attributes", {})
    if attributes.get("test_input_hidden") == "true":
        raise CapabilityError("Hidden test inputs cannot be overridden.")
    if attributes.get("test_input_read_only") == "true" or any(
        schema.get(key) is True for key in ("readonly", "readOnly", "read_only")
    ):
        raise CapabilityError("Read-only test inputs cannot be overridden.")
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict):
            raise CapabilityError("Object test inputs require native JSON objects.")
        names = {child["name"].split(".")[-1] for child in schema["children"]}
        if set(value) - names:
            raise CapabilityError("The test object contains undeclared fields.")
        for child in schema["children"]:
            name = child["name"].split(".")[-1]
            if name not in value:
                if child.get("attributes", {}).get("test_input_hidden") == "true":
                    continue
                if child.get("mandatory") is True:
                    raise CapabilityError("A required nested test input is missing.")
                raise CapabilityError(
                    "Supply every visible nested subflow test input explicitly, including optional fields. No test default is selected implicitly."
                )
            _validate_test_value(child, value[name])
        return
    if kind in _TEST_ARRAYS:
        if not isinstance(value, list):
            raise CapabilityError("Array test inputs require native JSON arrays.")
        if schema.get("mandatory") is True and not value:
            raise CapabilityError("A mandatory test array is empty.")
        for element in value:
            _validate_test_value(schema["children"][0], element)
        return
    if (
        isinstance(value, dict | list)
        or isinstance(value, str)
        and ("{{" in value or "}}" in value or "javascript:" in value.lower())
    ):
        raise CapabilityError(
            "Test inputs require static native values without scripts or pills."
        )
    if kind == "boolean" and type(value) is not bool:
        raise CapabilityError("Boolean test inputs require JSON booleans.")
    if kind in {"integer", "long"} and type(value) is not int:
        raise CapabilityError("Integer test inputs require JSON integers.")
    if kind in {
        "string",
        "choice",
        "reference",
        "document_id",
        "table_name",
        "field_name",
        "json",
    } and not isinstance(value, str):
        raise CapabilityError("This test input requires a native string.")
    data.set_value({"name": schema["name"], "parameter": schema}, value)


def _test_modal_children(schema: dict[str, Any], value: Any) -> list[dict[str, Any]]:
    if schema["type"] == "object":
        entries = (
            (child, value[child["name"].split(".")[-1]])
            for child in schema["children"]
            if child["name"].split(".")[-1] in value
        )
    elif schema["type"] in _TEST_ARRAYS:
        entries = ((schema["children"][0], element) for element in value)
    else:
        return []
    result = []
    for child, child_value in entries:
        children = _test_modal_children(child, child_value)
        result.append(
            {
                "name": child["name"].split(".")[-1],
                "value": "" if child["type"] in _TEST_COMPLEX else child_value,
                **({"children": children} if children else {}),
            }
        )
    return result


def _test_output_map(flow: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    # Test-modal outputMap is not an authoring binding or raw complex JSON.
    # constructTestModalData:193691-193770 emits named children and blank empty arrays.
    # Array.Object rows repeat the declared item name: buildObjectArrayStructure:93333-93353.
    if flow["type"] != "subflow":
        return inputs
    result = {}
    for row in flow["inputs"]:
        name = row["name"]
        if name not in inputs:
            continue
        if data.value_type(row).kind not in _TEST_COMPLEX:
            result[name] = inputs[name]
            continue
        children = _test_modal_children(_test_schema(row), inputs[name])
        result[name] = {"children": children} if children else ""
    return result


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
                    "Supply every subflow test input explicitly, including optional inputs. No test default is selected implicitly."
                )
            continue
        value = provided[name]
        if flow["type"] == "flow" and kind in {"reference", "document_id"}:
            if not isinstance(value, str):
                raise CapabilityError(
                    "Trigger test records require a native sys_id string."
                )
            validate_sys_id(value)
        if flow["type"] == "subflow":
            _validate_test_value(_test_schema(row), value)
        else:
            if (
                tree.pills(value)
                or isinstance(value, dict | list)
                or isinstance(value, str)
                and ("{{" in value or "}}" in value)
            ):
                raise CapabilityError(
                    "Test inputs require static scalar native values."
                )
            probe = {
                "name": name,
                "parameter": {**copy.deepcopy(spec), "readonly": False},
            }
            if (
                spec.get("children")
                or kind in {"object", "glide_list", "records"}
                or kind.startswith("array.")
            ):
                raise CapabilityError(
                    "Complex/list trigger test inputs require another adapter.",
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
        flow.get("snapshot") is not False
        or flow.get("masterSnapshot") is not False
        or flow.get("jsonSnapshot") is True
        or flow.get("parentFlow")
    ):
        raise CapabilityError(
            "Lifecycle operations require the editable owner, not an immutable published snapshot.",
            "UNAVAILABLE",
        )
    if (
        target.scalar(before.get("sys_mod_count")) != command.expected_revision
        or target.scalar(before.get("sys_scope")) != command.expected_scope
    ):
        raise CapabilityError("The lifecycle target revision changed.", "STALE_PREVIEW")
    if any(
        target.scalar(before.get(field)) is None for field in lifecycle.HEADER_FIELDS
    ):
        raise CapabilityError("Lifecycle header evidence is incomplete.", "UNAVAILABLE")
    validation = None
    if command.action == "deactivate":
        if (
            flow.get("type") != "flow"
            or flow.get("active") is not True
            or (flow.get("status"), flow.get("isPublished"))
            not in {("published", True), ("draft", False)}
            or not isinstance(flow.get("masterSnapshotId"), str)
            or target.scalar(before["active"]) != "true"
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
            target.scalar(before["master_snapshot"]) != flow["masterSnapshotId"]
            or target.scalar(before["status"]) != flow["status"]
        ):
            raise CapabilityError(
                "The published Studio definition and lifecycle header disagree.",
                "UNAVAILABLE",
            )
    else:
        validation_flow = flow
        republish = (
            command.action in {"activate", "publish", "test"}
            and type(flow.get("active")) is bool
            and flow.get("status") in {"draft", "published"}
            and isinstance(flow.get("masterSnapshotId"), str)
            and bool(flow["masterSnapshotId"])
        )
        if republish:
            validate_sys_id(flow["masterSnapshotId"])
            validation_flow = _draft_view(flow)
            if target.scalar(before["master_snapshot"]) != flow["masterSnapshotId"]:
                raise CapabilityError(
                    "The previous published version differs from the lifecycle header.",
                    "UNAVAILABLE",
                )
        editing.require_editable(validation_flow, command.sys_id or "")
        if (
            target.scalar(before["active"]) != str(flow["active"]).lower()
            or target.scalar(before["status"]) != flow["status"]
            or target.scalar(before["type"]) != flow["type"]
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
                target.scalar(record.get("sys_id")) != inputs[name]
                or not (target.scalar(record.get("sys_mod_count")) or "").isdecimal()
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
        "verification": "offline_regression_and_bounded_development_evidence",
        "live_verification_scope": "tv2dev native release 27800/engine 2, owned future Run Once Log flow and scalar Log subflow, 2026-10-23. Not a verification of this target.",
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
    await target.require_current(command, client, context)
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
    wire = native.rest_payload(flow)
    wire.pop("labelCacheAsJsonString", None)
    payload = (
        {
            "flow": wire,
            "outputMap": _test_output_map(flow, current["inputs"]),
            "runOnThread": False,
        }
        if command.action == "test"
        else None
        if operation == "deactivate"
        else wire
    )
    accepted_context: str | None = None
    phase = "effect.request"
    echo_engine_unset = False
    runtime_version = None
    try:
        echo = await client.studio_lifecycle(flow["id"], operation, payload)
        if command.action == "test":
            validate_sys_id(echo)
            accepted_context = echo
            phase = "test.context"
            check_table_access("sys_flow_context")
            execution = await client.flow_record(
                "sys_flow_context", echo, lifecycle.EXECUTION_FIELDS
            )
            if target.scalar(execution.get("sys_id")) != echo:
                raise ValueError("Unrelated or incomplete test context")
            snapshot_evidence, diagnostics = await lifecycle.execution_snapshot(
                execution, flow["id"], client
            )
            context_flow = target.scalar(execution.get("flow"))
            owner_pending = context_flow in {None, ""}
            if not owner_pending and context_flow != flow["id"]:
                validate_sys_id(context_flow or "")
                check_table_access("sys_hub_flow_snapshot")
                ancestor = await client.studio_request(
                    f"flow/{context_flow}", minimal=True
                )
                if (
                    ancestor.get("id") != context_flow
                    or ancestor.get("parentFlow") != flow["id"]
                    or ancestor.get("snapshot") is not True
                ):
                    raise ValueError("Unrelated test context")
            context_state = target.scalar(execution.get("state"))
            if context_state not in {None, "", *lifecycle.EXECUTION_STATES}:
                raise ValueError("Unrecognized execution state")
            if owner_pending or context_state in {None, ""}:
                diagnostics.append(
                    lifecycle.diagnostic(
                        "PENDING_EXECUTION",
                        "execution",
                        "Execution owner or state is not yet available. Do not replay the test.",
                        incomplete=True,
                    )
                )
            phase = "test.draft"
            actual = await client.studio_request(f"flow/{flow['id']}")
            semantics.compare_saved(
                flow,
                actual,
                generated_ids=set(),
                allow_active_draft=active_drafts.eligible(flow),
            )
            phase = "test.header"
            if current["header"] != await client.flow_record(
                "sys_hub_flow", flow["id"], lifecycle.HEADER_FIELDS
            ):
                raise ValueError("Test changed draft state")
            await target.require_current(command, client, context)
            return {
                "action": "test",
                "sys_id": flow["id"],
                "execution_sys_id": echo,
                **snapshot_evidence,
                "complete": not diagnostics,
                "diagnostics": diagnostics,
                "state": context_state,
                "context_owner_verified": not owner_pending,
                "terminal": (context_state in {"COMPLETE", "ERROR", "CANCELLED"})
                if not owner_pending and context_state in lifecycle.EXECUTION_STATES
                else None,
                "execution_succeeded": (context_state == "COMPLETE")
                if not owner_pending
                and context_state in {"COMPLETE", "ERROR", "CANCELLED"}
                else None,
                "follow_up": "Use flow.status with this exact execution_sys_id. Do not replay the test.",
                "version_content_verified": False,
            }
        if not isinstance(echo, dict) or echo.get("id") != flow["id"]:
            raise ValueError("Lifecycle identity")
        actual = await client.studio_request(f"flow/{flow['id']}")
        phase = "publication.content"
        echo_engine_unset = (
            type(echo.get("flowEngineVersion")) is int
            and echo.get("flowEngineVersion") == 0
            and flow.get("flowEngineVersion") == actual.get("flowEngineVersion") == 2
            and active_drafts.eligible(actual)
        )
        comparison_echo = copy.deepcopy(echo)
        if echo_engine_unset:
            # The captured native publish response leaves this getter at zero.
            # The owner GET and selected published version must independently
            # establish engine 2; no persisted engine change is normalized.
            comparison_echo["flowEngineVersion"] = 2
        comparison_before = copy.deepcopy(flow)
        native_release = (
            actual.get("authoredOnReleaseVersion") == active_drafts.NATIVE_RELEASE
        )
        if command.action in {"activate", "publish"} and native_release:
            elapsed = actual.get("attributes", {}).get("timeFromCreateToActivate")
            if elapsed is not None:
                if (
                    not isinstance(elapsed, str)
                    or not elapsed.isascii()
                    or not elapsed.isdecimal()
                    or echo.get("attributes", {}).get("timeFromCreateToActivate")
                    != elapsed
                ):
                    raise ValueError("Publication timing metadata is inconsistent")
                # This server-owned elapsed-time metric is not authored content.
                comparison_before.setdefault("attributes", {})[
                    "timeFromCreateToActivate"
                ] = elapsed
        if (
            command.action == "deactivate"
            and native_release
            and echo.get("userHasRolesAssignedToFlow") is False
            and actual.get("userHasRolesAssignedToFlow")
            is flow.get("userHasRolesAssignedToFlow")
            is True
        ):
            # Native deactivate leaves a false UI permission getter in its echo.
            # Stored runAs/runWithRoles remain strict in both comparisons.
            comparison_echo["userHasRolesAssignedToFlow"] = True
        _compare_content(comparison_before, comparison_echo)
        _compare_content(comparison_before, actual)
        semantics.compare_saved(
            _draft_view(comparison_echo), _draft_view(actual), generated_ids=set()
        )
        if any(
            echo.get(k) != actual.get(k) or type(echo.get(k)) is not type(actual.get(k))
            for k in LIFECYCLE_FIELDS
        ):
            raise ValueError("Lifecycle echo drift")
        active = command.action != "deactivate"
        if actual.get("active") is not active:
            raise ValueError("Unexpected activation state")
        native_owner = active_drafts.eligible(actual)
        if (
            flow.get("authoredOnReleaseVersion") == active_drafts.NATIVE_RELEASE
            and flow.get("flowEngineVersion") == 2
            and not native_owner
        ):
            raise ValueError("Native lifecycle owner/version evidence is incomplete")
        if (
            command.action != "deactivate"
            and not native_owner
            and (
                actual.get("status") != "published"
                or actual.get("isPublished") is not True
            )
        ):
            raise ValueError("Publication not established")
        phase = "publication.header"
        header = await client.flow_record(
            "sys_hub_flow", flow["id"], lifecycle.HEADER_FIELDS
        )
        if (
            target.scalar(header.get("sys_id")) != flow["id"]
            or target.scalar(header.get("active")) != str(active).lower()
            or target.scalar(header.get("sys_scope")) != command.expected_scope
            or target.scalar(header.get("type")) != flow["type"]
            or target.scalar(header.get("status")) != actual.get("status")
            or target.scalar(header.get("master_snapshot"))
            != actual.get("masterSnapshotId")
        ):
            raise ValueError("Independent lifecycle header mismatch")
        if native_owner:
            phase = "publication.snapshot"
            runtime = await active_drafts.read(
                command,
                actual,
                client,
                revision=target.scalar(header.get("sys_mod_count")),
                require_save_profile=False,
            )
            runtime_version = runtime["snapshot"]["id"]
        if command.action != "deactivate":
            snapshot = actual.get("masterSnapshotId")
            if not isinstance(snapshot, str):
                raise ValueError("Missing snapshot identity")
            validate_sys_id(snapshot)
            if target.scalar(
                header.get("master_snapshot")
            ) != snapshot or target.scalar(header.get("status")) != actual.get(
                "status"
            ):
                raise ValueError("Snapshot header mismatch")
            check_table_access("sys_hub_flow_snapshot")
            phase = "publication.snapshot"
            if not native_owner:
                published = await client.studio_request(
                    f"flow/{snapshot}", minimal=True
                )
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
    except Exception as exc:  # noqa: BLE001 - contain ambiguous effects without exposing payloads or retrying
        raise CapabilityError(
            f"Lifecycle result or independent readback is incomplete ({phase}, {exc.detail() if isinstance(exc, semantics.SemanticMismatch) else type(exc).__name__}). UNKNOWN_OUTCOME: inspect flow.status before another preview. "
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
        "runtime_snapshot_sys_id": runtime_version,
        "native_echo_engine_unset": echo_engine_unset,
        "execution_requested_directly": False,
        "triggers_enabled": active if flow["type"] == "flow" else None,
    }
