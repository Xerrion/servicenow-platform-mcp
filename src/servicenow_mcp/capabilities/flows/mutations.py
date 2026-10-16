"""Experimental draft authoring through the observed Workflow Studio contract."""

import copy
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError, validate_payload
from servicenow_mcp.capabilities.flows import capture, editing, semantics
from servicenow_mcp.capabilities.mutations.models import FlowCommand, fingerprint
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.validation import sanitize_query_value, validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient


async def create_properties(
    command: FlowCommand, client: ServiceNowClient
) -> dict[str, Any]:
    check_table_access("sys_scope")
    scope = await client.get_record(
        "sys_scope", command.scope or "", fields=["sys_id", "name"]
    )
    if scope.get("sys_id") != command.scope:
        raise CapabilityError("The requested application scope could not be resolved.")
    matches = await client.query_records(
        "sys_hub_flow",
        f"name={sanitize_query_value(command.name or '')}^sys_scope={command.scope}",
        fields=["sys_id", "name"],
        limit=1,
    )
    if matches["records"]:
        raise CapabilityError(
            "A flow with this name already exists in the requested scope.",
            "DUPLICATE_FLOW",
        )
    return {
        "name": command.name,
        "description": command.description or "",
        "scope": command.scope,
        "type": "flow",
        "active": False,
        "status": "draft",
        "runAs": "user",
        "access": "public",
        "flowPriority": "MEDIUM",
        "label_cache": [],
    }


def require_editable_draft(flow: dict[str, Any], sys_id: str) -> None:
    validate_payload(flow, field_name="flow", validate_keys=False)
    required_arrays = (
        "actionInstances",
        "triggerInstances",
        "flowLogicInstances",
        "subFlowInstances",
        "inputs",
        "outputs",
        "flowVariables",
        "label_cache",
    )
    if any(not isinstance(flow.get(key), list) for key in required_arrays):
        raise CapabilityError(
            "Studio returned an incomplete flow definition. No draft write is allowed."
        )
    if (
        flow.get("snapshot") is not False
        or flow.get("masterSnapshot") is not False
        or flow.get("remoteTriggerSysId")
    ):
        raise CapabilityError(
            "Snapshots and remotely triggered flows cannot be edited by draft authoring."
        )
    if flow.get("flowEngineVersion") != 2:
        raise CapabilityError(
            "Only V2 draft definitions are supported by this adapter."
        )
    if any(
        flow.get(key)
        for key in (
            "contentACLFilteredOrders",
            "nonCriticalErrors",
            "deletedFlowLogicInstances",
        )
    ):
        raise CapabilityError(
            "Studio returned a partial or unsupported flow definition."
        )
    if (
        flow.get("id") != sys_id
        or flow.get("type") != "flow"
        or flow.get("active") is not False
    ):
        raise CapabilityError("Only inactive flows can be edited by draft authoring.")
    if (
        flow.get("status") != "draft"
        or flow.get("isPublished") is not False
        or flow.get("masterSnapshotId")
    ):
        raise CapabilityError("Published flows are not supported by draft authoring.")
    if flow.get("security", {}).get("can_write") is not True:
        raise CapabilityError("Studio does not report write access to this flow.")
    semantics.studio_semantics(flow)
    # The initial adapter cannot safely round-trip data pills, complex logic, or partial ACL views.
    if any(
        flow.get(k)
        for k in (
            "triggerInstances",
            "flowLogicInstances",
            "inputs",
            "outputs",
            "flowVariables",
            "stages",
            "label_cache",
        )
    ):
        raise CapabilityError(
            "This experimental adapter supports only linear drafts without data pills."
        )
    if flow.get("startingIndexOfErrorHandlingInstances", -1) != -1:
        raise CapabilityError(
            "Flows with error handlers are not supported by draft authoring."
        )
    for action in flow["actionInstances"] + flow["subFlowInstances"]:
        definition = action.get("actionType", action.get("subFlow", {}))
        if (
            action.get("parent")
            or action.get("deleted")
            or definition.get("userCanRead") is not True
        ):
            raise CapabilityError(
                "The flow contains an unsupported or unreadable action."
            )
        if (
            "subflowSysId" in action
            and action.get("waitForCompletion", {}).get("value") is not True
        ):
            raise CapabilityError(
                "Only subflow steps that wait for completion are supported."
            )
        for item in action.get("inputs", []):
            if (
                item.get("scriptActive")
                or item.get("children")
                or isinstance(item.get("value"), dict | list)
            ):
                raise CapabilityError(
                    "Existing action inputs must contain only static scalar values."
                )
            if isinstance(item.get("value"), str) and "{{" in item["value"]:
                raise CapabilityError(
                    "Existing data pills are not supported by draft authoring."
                )


def action_inputs(
    command: FlowCommand, definition: dict[str, Any]
) -> list[dict[str, Any]]:
    if command.action == "add_subflow":
        if (
            definition.get("type") != "subflow"
            or definition.get("parentFlow") != command.subflow_sys_id
            or definition.get("snapshot") is not True
            or definition.get("masterSnapshot") is not True
        ):
            raise CapabilityError(
                "subflow_sys_id must resolve to a published subflow snapshot."
            )
    elif (
        definition.get("id") != command.action_type_sys_id
        or definition.get("state") != "published"
        or definition.get("is_snapshot") is not True
    ):
        raise CapabilityError(
            "action_type_sys_id must identify a published action snapshot."
        )
    entitled = (
        definition.get("isFlowEntitled")
        if command.action == "add_subflow"
        else definition.get("entitled")
    )
    if (
        definition.get("security", {}).get("can_read") is not True
        or entitled is not True
    ):
        raise CapabilityError("The action must be readable and entitled.")
    parameters = definition.get("inputs")
    if not isinstance(parameters, list):
        raise CapabilityError("The action response does not contain input metadata.")
    provided = command.inputs or {}
    if set(provided) - {p["name"] for p in parameters}:
        raise CapabilityError("inputs contains an unknown action input name.")
    result = []
    for parameter in parameters:
        name = parameter["name"]
        value = provided.get(name, parameter.get("defaultValue", ""))
        kind = parameter.get("type")
        if parameter.get("children") or kind not in {
            "string",
            "choice",
            "boolean",
            "integer",
        }:
            raise CapabilityError(
                "Only string, choice, boolean, and integer action inputs are supported."
            )
        if parameter.get("mandatory") and value in (None, ""):
            raise CapabilityError("A mandatory action input is missing.")
        display = value
        if kind in {"string", "choice"}:
            if not isinstance(value, str) or "{{" in value:
                raise CapabilityError(
                    "String and choice inputs require static strings."
                )
            maximum = parameter.get("maxsize", 0)
            if maximum and len(value) > int(maximum):
                raise CapabilityError("An action input exceeds its maximum length.")
        if kind == "choice":
            choices = {c["value"]: c["label"] for c in parameter.get("choices", [])}
            if value not in choices:
                raise CapabilityError("An action input is not an allowed choice value.")
            display = choices[value]
        elif kind == "boolean" and not isinstance(value, bool):
            raise CapabilityError("Boolean action inputs require JSON booleans.")
        elif kind == "integer" and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            raise CapabilityError("Integer action inputs require JSON integers.")
        result.append(
            {
                "id": parameter["id"],
                "name": name,
                "value": value,
                "displayValue": display,
                "children": [],
                "parameter": parameter,
                "scriptActive": False,
            },
        )
    return result


async def action_state(
    command: FlowCommand, client: ServiceNowClient
) -> tuple[dict[str, Any], dict[str, Any]]:
    check_table_access(
        "sys_hub_flow_snapshot"
        if command.action == "add_subflow"
        else "sys_hub_action_type_snapshot"
    )
    flow = await client.studio_request(f"flow/{command.sys_id}")
    require_editable_draft(flow, command.sys_id or "")
    if command.action == "add_subflow":
        if command.subflow_sys_id == command.sys_id:
            raise CapabilityError("A flow cannot call itself through draft authoring.")
        parent = await client.get_record(
            "sys_hub_flow",
            command.subflow_sys_id or "",
            fields=["sys_id", "type", "active", "master_snapshot", "sys_scope"],
        )
        snapshot_id = parent.get("master_snapshot")
        if isinstance(snapshot_id, dict):
            snapshot_id = snapshot_id.get("value")
        if (
            parent.get("type") != "subflow"
            or parent.get("active") not in (True, "true")
            or not snapshot_id
        ):
            raise CapabilityError(
                "The requested subflow has no active published version."
            )
        validate_sys_id(snapshot_id)
        definition = await client.studio_request(f"flow/{snapshot_id}", minimal=True)
        if definition.get("id") != snapshot_id:
            raise CapabilityError("Studio returned a different subflow snapshot.")
        if (
            definition.get("scope") != flow.get("scope")
            and definition.get("access") != "public"
        ):
            raise CapabilityError(
                "The published subflow is not accessible from the target scope."
            )
    else:
        definition = await client.studio_request(
            f"action/action_types/{command.action_type_sys_id}"
        )
    validate_payload(definition, field_name="action", validate_keys=False)
    action_inputs(command, definition)
    return flow, definition


async def preview(
    command: FlowCommand, client: ServiceNowClient
) -> tuple[dict[str, Any], str | None]:
    context = await capture.read_binding(command, client)
    summary, revision = await _preview(command, client)
    summary["context"] = {
        "state": "bound" if context else "unverified",
        "expected_scope": command.expected_scope,
        "expected_update_set": command.expected_update_set,
        "capture_scope": "flow_header_metadata_only",
    }
    return summary, fingerprint({"operation": revision, "context": context})


async def _preview(
    command: FlowCommand, client: ServiceNowClient
) -> tuple[dict[str, Any], str | None]:
    if command.action == "edit":
        return await editing.preview(command, client)
    if command.action == "create":
        payload = await create_properties(command, client)
        return mask_sensitive_fields(payload), fingerprint(payload)
    flow, definition = await action_state(command, client)
    return {
        "sys_id": flow["id"],
        "name": flow["name"],
        "active": False,
        "status": "draft",
        **(
            {
                "subflow_sys_id": command.subflow_sys_id,
                "snapshot_sys_id": definition["id"],
                "wait_for_completion": True,
            }
            if command.action == "add_subflow"
            else {"action_type_sys_id": definition["id"]}
        ),
        "action_name": definition["name"],
        "inputs": mask_sensitive_fields(
            {item["name"]: item["value"] for item in action_inputs(command, definition)}
        ),
        "existing_action_count": len(flow["actionInstances"]),
        "existing_subflow_count": len(flow["subFlowInstances"]),
        "proposed_order": max(
            [
                int(step["order"])
                for step in flow["actionInstances"] + flow["subFlowInstances"]
            ],
            default=0,
        )
        + 1,
    }, fingerprint([flow, definition])


async def apply(
    command: FlowCommand, expected: str | None, client: ServiceNowClient
) -> dict[str, Any]:
    context = await capture.read_binding(command, client)
    _, revision = await _preview(command, client)
    if fingerprint({"operation": revision, "context": context}) != expected:
        raise CapabilityError(
            "The flow, dependencies, or application/update-set context changed. Create a new preview.",
            "STALE_PREVIEW",
        )
    result = await _apply(command, revision, client, binding_context=context)
    result["capture"] = await capture.verify_after(
        command, client, result["sys_id"], context
    )
    return result


async def _apply(
    command: FlowCommand,
    expected: str | None,
    client: ServiceNowClient,
    *,
    binding_context: dict[str, Any] | None,
) -> dict[str, Any]:
    if command.action == "edit":
        return await editing.apply(
            command, expected, client, binding_context=binding_context
        )
    collection = (
        "subFlowInstances" if command.action == "add_subflow" else "actionInstances"
    )
    if command.action == "create":
        payload = await create_properties(command, client)
        if fingerprint(payload) != expected:
            raise CapabilityError(
                "The creation properties changed. Create a new preview.",
                "STALE_PREVIEW",
            )
        await capture.require_current(command, client, binding_context)
        saved = await semantics.save_draft(client, payload, create=True)
    else:
        flow, definition = await action_state(command, client)
        if fingerprint([flow, definition]) != expected:
            raise CapabilityError(
                "The flow or action changed since preview. Create a new preview.",
                "STALE_PREVIEW",
            )
        payload = copy.deepcopy(flow)
        orders = [
            int(a["order"]) for a in flow["actionInstances"] + flow["subFlowInstances"]
        ]
        instance = {
            "id": command.instance_id,
            "flowSysId": flow["id"],
            "order": str(max(orders, default=0) + 1),
            "uiUniqueIdentifier": command.ui_id,
            "deleted": False,
            "parent": "",
            "metadata": {},
            "inputs": action_inputs(command, definition),
            "type": "subflow" if command.action == "add_subflow" else "action",
            "snapshot": False,
            "name": definition["name"],
            "internalName": definition.get(
                "internalName", definition.get("internal_name", "")
            ),
        }
        if command.action == "add_subflow":
            instance.update(
                {
                    "subflowSysId": definition["id"],
                    "subFlow": definition,
                    "waitForCompletion": {
                        "name": "wait_for_completion",
                        "type": "boolean",
                        "value": True,
                    },
                },
            )
        else:
            instance.update(
                {
                    "actionTypeSysId": definition["id"],
                    "actionType": definition,
                    "parentActionTypeId": definition.get("parent_action", ""),
                },
            )
        payload[collection].append(instance)
        validate_payload(payload, field_name="flow", validate_keys=False)
        semantics.studio_semantics(payload)
        await capture.require_current(command, client, binding_context)
        saved = await semantics.save_draft(client, payload)
    if (
        saved.get("active") is not False
        or saved.get("status") != "draft"
        or (command.sys_id and saved["id"] != command.sys_id)
        or (
            command.action == "create"
            and (
                saved.get("scope") != command.scope or saved.get("name") != command.name
            )
        )
    ):
        raise CapabilityError(
            "The saved flow differs from the draft request. Check ServiceNow before retrying.",
            "UNKNOWN_OUTCOME",
        )
    action_sys_id = None
    if command.action == "create":
        saved = await semantics.verify_created(client, payload, saved)
    else:
        saved = await semantics.verify_saved(
            client, payload, saved, generated_ids={command.instance_id}
        )
        action_sys_id = next(
            row["id"]
            for row in saved[collection]
            if row.get("uiUniqueIdentifier") == command.ui_id
        )
    return {
        "action": command.action,
        "table": "sys_hub_flow",
        "sys_id": saved["id"],
        "name": saved.get("name"),
        "active": False,
        "status": "draft",
        **(
            {
                "subflow_instance_sys_id"
                if command.action == "add_subflow"
                else "action_sys_id": action_sys_id
            }
            if action_sys_id is not None
            else {}
        ),
    }
