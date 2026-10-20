"""Published call definitions and native step construction for ordered draft edits."""

import copy
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError, validate_payload
from servicenow_mcp.capabilities.flows import data, tree
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient


async def definition(
    flow: dict[str, Any], identity: str, *, subflow: bool, client: ServiceNowClient
) -> dict[str, Any]:
    validate_sys_id(identity)
    check_table_access(
        "sys_hub_flow_snapshot" if subflow else "sys_hub_action_type_snapshot"
    )
    if subflow:
        if identity == flow["id"]:
            raise CapabilityError("A flow cannot call itself.")
        check_table_access("sys_hub_flow")
        parent = await client.get_record(
            "sys_hub_flow",
            identity,
            fields=["sys_id", "type", "active", "master_snapshot", "sys_scope"],
        )
        snapshot = parent.get("master_snapshot")
        if isinstance(snapshot, dict):
            snapshot = snapshot.get("value")
        if (
            parent.get("sys_id") != identity
            or parent.get("type") != "subflow"
            or parent.get("active") not in (True, "true")
            or not isinstance(snapshot, str)
        ):
            raise CapabilityError("The subflow has no active published snapshot.")
        validate_sys_id(snapshot)
        result = await client.studio_request(f"flow/{snapshot}", minimal=True)
        if (
            result.get("id") != snapshot
            or result.get("parentFlow") != identity
            or result.get("type") != "subflow"
            or result.get("snapshot") is not True
            or result.get("masterSnapshot") is not True
        ):
            raise CapabilityError(
                "The subflow snapshot identity or publication state is incomplete."
            )
        if (
            result.get("scope") != flow.get("scope")
            and result.get("access") != "public"
        ):
            raise CapabilityError(
                "The subflow is not accessible from the target scope."
            )
    else:
        result = await client.studio_request(f"action/action_types/{identity}")
        if (
            result.get("id") != identity
            or result.get("state") != "published"
            or result.get("is_snapshot") is not True
        ):
            raise CapabilityError(
                "Action insertion requires a published action snapshot."
            )
    if (
        result.get("security", {}).get("can_read") is not True
        or result.get("isFlowEntitled" if subflow else "entitled") is not True
    ):
        raise CapabilityError("The call definition is not readable and entitled.")
    validate_payload(result, field_name="call", validate_keys=False)
    return result


def step(
    flow: dict[str, Any],
    definition: dict[str, Any],
    inputs: dict[str, Any],
    *,
    subflow: bool,
    uid: str,
) -> tree.Step:
    """buildNewActionInstance/buildNewSubflowInstance:156128-156263."""
    parameters = definition.get("inputs")
    if not isinstance(parameters, list) or any(
        not isinstance(p.get("name"), str) for p in parameters
    ):
        raise CapabilityError("The call input schema is incomplete.", "UNAVAILABLE")
    if set(inputs) - {p["name"] for p in parameters}:
        raise CapabilityError("The call contains an unknown input.")
    bindings = []
    for parameter in parameters:
        if parameter.get("dependent_on") or parameter.get("dynamic"):
            raise CapabilityError(
                "Dynamic or dependent call inputs require another insertion adapter.",
                "UNAVAILABLE",
            )
        value = inputs.get(parameter["name"], parameter.get("defaultValue", ""))
        if (
            data.value_type(parameter).kind == "boolean"
            and parameter["name"] not in inputs
        ):
            value = value in ("true", "1", True)
        binding = {
            **copy.deepcopy(parameter),
            "name": parameter["name"],
            "parameter": copy.deepcopy(parameter),
            "value": "",
            "children": [],
            "scriptActive": False,
        }
        data.set_value(binding, value)
        bindings.append(binding)
    raw = {
        "uiUniqueIdentifier": uid,
        "flowSysId": flow["id"],
        "type": "subflow" if subflow else "action",
        "name": definition["name"],
        "internalName": definition.get(
            "internalName", definition.get("internal_name", "")
        ),
        "inputs": bindings,
        "metadata": {},
        "deleted": False,
        "snapshot": False,
        "isNew": True,
        "isDirty": False,
        "errorHandlingInstance": False,
    }
    if subflow:
        raw.update(
            subflowSysId=definition["id"],
            subFlow=copy.deepcopy(definition),
            waitForCompletion={
                "name": "wait_for_completion",
                "type": "boolean",
                "value": True,
            },
        )
    else:
        raw.update(
            actionTypeSysId=definition["id"],
            actionType=copy.deepcopy(definition),
            parentActionTypeId=definition.get("parent_action", ""),
        )
    return tree.Step(raw, "subflow" if subflow else "action")
